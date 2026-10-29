"""Classificacao CANONICA de grupos musculares com MySQL real (PyMySQL):
agregado de `app/muscle_groups_mapped.py` (Grupos musculares, Dashboard e
Insights) e contrato HTTP de `/analytics/muscle-groups-mapped-table`.
A paridade com o caminho de producao (Data API) fica em
tests/integration/dataapi/test_muscle_groups_mapped_paridade.py."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import muscle_groups_mapped as mgm
from app.security import Identity, get_current_identity
from tests.support.muscle_groups_mapped import (
    SUPINO_ID,
    expected_summary,
    raw_connection,
    seed_canonical_history,
    table_summary,
)


@pytest.fixture
def history(mysql_schema):
    with raw_connection(mysql_schema) as conn:
        return seed_canonical_history(conn)


@pytest.mark.parametrize("include_secondary", [False, True])
@pytest.mark.parametrize("days", [30, 90])
def test_aggregate_series_volume_rpe_e_nao_mapeados(history, days, include_secondary):
    result = mgm.aggregate("weekly", "volume", days, include_secondary, user_id=history["user"])
    assert table_summary(result) == expected_summary(days, include_secondary)


def test_trend_soma_o_mesmo_que_a_tabela(history):
    result = mgm.aggregate("daily", "volume", 30, False, user_id=history["user"])
    totals: dict = {}
    for row in result["trend_rows"]:
        entry = totals.setdefault(row["group"], {"sets": 0, "volume_kg": 0.0})
        entry["sets"] += row["sets"]
        entry["volume_kg"] += row["volume_kg"]
    assert totals == {row["group"]: {"sets": row["sets"], "volume_kg": row["volume_kg"]} for row in result["table_rows"]}
    # Costas treinado nos 2 dias; RPE medio por dia.
    costas = {row["period"]: row["avg_rpe"] for row in result["trend_rows"] if row["group"] == "Costas"}
    assert costas == {history["date_a"].isoformat(): 8.33, history["date_b"].isoformat(): 9.0}


def test_consulta_devolve_linhas_agregadas_nao_uma_por_serie(history):
    rows, meta = mgm.load_rows(30, user_id=history["user"])
    assert meta["ok"] is True
    # 10 series -> 5 linhas (dia, exercicio exibido, grupos).
    assert sum(int(row["set_count"]) for row in rows) == 10
    assert sorted((str(row["activity_date"]), row["exercise_name"]) for row in rows) == sorted(
        [
            (history["date_a"].isoformat(), "Lat Pulldown"),
            (history["date_a"].isoformat(), "Supino reto"),
            (history["date_a"].isoformat(), "Rosca livre XYZ"),
            (history["date_a"].isoformat(), "Alongamento"),
            (history["date_b"].isoformat(), "Lat Pulldown"),
        ]
    )


def test_blocos_de_dias_nao_mudam_o_resultado(history, monkeypatch):
    whole = mgm.aggregate("weekly", "volume", 90, True, user_id=history["user"])
    monkeypatch.setattr(mgm, "LOAD_ROWS_CHUNK_DAYS", 3)
    chunked = mgm.aggregate("weekly", "volume", 90, True, user_id=history["user"])
    assert chunked == whole


def test_nomes_que_diferem_so_por_caixa_nao_se_fundem(history, mysql_schema):
    with raw_connection(mysql_schema) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO exercises (activity_id, sequence, original_name, mapping_id, sets_count) VALUES (%s, 9, 'SUPINO RETO', %s, 1)",
            (history["activity_a"], SUPINO_ID),
        )
        cur.execute(
            "INSERT INTO exercise_sets (exercise_id, set_number, reps, weight_kg, rpe, set_type) VALUES (%s, 1, 5, '70.00', '9.00', 'working')",
            (cur.lastrowid,),
        )
    result = mgm.aggregate("weekly", "volume", 30, False, user_id=history["user"])
    peitoral = next(row for row in result["table_rows"] if row["group"] == "Peitoral")
    assert peitoral["exercises"] == ["SUPINO RETO", "Supino reto"]
    assert peitoral["sets"] == 4


def test_rota_mapped_table_expoe_campos_do_dashboard(history):
    app = FastAPI()
    app.include_router(mgm.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=history["user"], role="regular", source="session")
    client = TestClient(app)

    resp = client.get("/analytics/muscle-groups-mapped-table?period=yearly&metric=volume&days=30&include_secondary=false")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "Deprecation" not in resp.headers
    assert (body["total_sets"], body["mapped_sets"], body["unmapped_sets"], body["mapped_ratio"]) == (10, 7, 3, 0.7)
    assert [
        (row["group"], row["sets"], row["volume_kg"], row["avg_rpe"]) for row in body["table_rows"]
    ] == [("Peitoral", 3, 1620.0, 8.25), ("Costas", 4, 1095.0, 8.5), ("Outros", 3, 240.0, 8.0)]
    assert body["unmapped"] == [{"exercise": "Rosca livre XYZ", "sets": 2}, {"exercise": "Alongamento", "sets": 1}]
