"""Fase 5, PR 4 - técnica avançada no volume realizado, contra MySQL real nos
DOIS backends (PyMySQL e shim da RDS Data API).

Uma sessão de supino: aquecimento + 2 séries de trabalho + 2 mini-blocos de
rest-pause de 5 reps numa âncora de 10 (um gravado com técnica/âncora, outro
LEGADO, sem nenhum dos dois - derivado). O bloco vale 1,0 série; o aquecimento
0. Então o peitoral recebe 3 séries equivalentes - e esse MESMO número tem de
aparecer em todas as leituras de volume realizado por grupo: Novo Dashboard
(resumo, comparativo por sessão, drill-down), overview de recuperação,
tendência semanal (alertas de volume), janela de ativação diária (Fase 7) e
estímulo da sessão na aderência. Dois números diferentes na tela para o mesmo
treino são exatamente o defeito que este PR não pode criar.
"""

from __future__ import annotations

import datetime as _dt

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import advanced_techniques, analytics_dashboard
from app.intelligence.dashboard import adherence_service, muscle_summary_service  # noqa: F401 - precisa estar importado
from app.intelligence.muscle_recovery_service import MuscleRecoveryService
from app.intelligence.volume_service import VolumeService
from app.security import Identity, get_current_identity
from tests.support.isolamento_v3 import congelar_relogio, truncar
from tests.support.relogio_usuario import hoje_do_usuario

HOJE = hoje_do_usuario()
SUPINO = 2101

# (set_number, set_type, reps, peso, rpe, technique_code, anchor_set_number)
SERIES = [
    (1, "warmup", 12, "20.00", None, None, None),
    (2, "working", 10, "60.00", "9.00", None, None),
    (3, "working", 10, "60.00", "10.00", None, None),
    (4, "rest_pause", 5, "60.00", "10.00", "rest_pause", 3),
    # Legado (Zepp / antes da 059): sem técnica nem âncora - derivados.
    (5, "rest_pause", 5, "60.00", "10.00", None, None),
]
ESPERADO_PEITORAL = 3.0  # 2 de trabalho + bloco de 1,0
PESOS = {"Peitoral": 1.0, "Triceps": 0.5, "Ombro": 0.25}


@pytest.fixture
def seed(raw):
    truncar(raw)
    advanced_techniques.clear_cache()
    ids: dict[str, int] = {}
    with raw.cursor() as cur:
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES ('dono_f5', 'x', 'admin')")
        ids["dono"] = cur.lastrowid
        for grupo in PESOS:
            cur.execute("INSERT INTO muscle_groups (name) VALUES (%s)", (grupo,))
            ids[grupo] = cur.lastrowid
        cur.execute(
            "INSERT INTO exercise_zepp_mappings (id, exercise_name, amazfit_name) VALUES (%s, 'Supino reto', 'Supino reto')",
            (SUPINO,),
        )
        cur.execute("INSERT INTO exercise_catalog (id, name) VALUES (%s, 'Supino reto')", (SUPINO,))
        cur.executemany(
            "INSERT INTO exercise_muscle_contributions "
            "(exercise_id, muscle_group_id, role, stimulus_weight, fatigue_weight) VALUES (%s,%s,%s,%s,%s)",
            [
                (SUPINO, ids["Peitoral"], "PRIMARY", 1.0, 1.0),
                (SUPINO, ids["Triceps"], "SECONDARY", 0.5, 0.4),
                (SUPINO, ids["Ombro"], "STABILIZER", 0.25, 0.2),
            ],
        )
        dia = HOJE - _dt.timedelta(days=1)
        cur.execute(
            "INSERT INTO activities (user_id, local_date, started_at, name, activity_type, session_status) "
            "VALUES (%s, %s, %s, 'Treino', 'Musculação', 'done')",
            (ids["dono"], dia, _dt.datetime.combine(dia, _dt.time(19, 0))),
        )
        ids["activity"] = cur.lastrowid
        # `exercises.sets_count` como a API grava: séries NÃO preparatórias.
        cur.execute(
            "INSERT INTO exercises (activity_id, sequence, original_name, mapping_id, sets_count) "
            "VALUES (%s, 1, 'Supino reto', %s, 4)",
            (ids["activity"], SUPINO),
        )
        exercise_row_id = cur.lastrowid
        cur.executemany(
            "INSERT INTO exercise_sets (exercise_id, set_number, set_type, reps, weight_kg, rpe, "
            "technique_code, anchor_set_number) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            [(exercise_row_id, *serie) for serie in SERIES],
        )
    yield ids
    advanced_techniques.clear_cache()


def _client(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(analytics_dashboard.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=user_id, role="regular", source="session")
    return TestClient(app, raise_server_exceptions=False)


def _por_grupo(items, chave="muscle_group_id"):
    return {item[chave]: item for item in items}


def test_mesmo_numero_em_todas_as_leituras_de_volume_realizado(seed, db_backend, monkeypatch):
    congelar_relogio(monkeypatch)
    dono = seed["dono"]
    client = _client(dono)

    # --- Novo Dashboard: resumo + comparativo por sessão -------------------
    corpo = client.get("/analytics/dashboard/muscles", params={"window": "28d", "include_comparatives": "true"})
    assert corpo.status_code == 200, corpo.text
    itens = _por_grupo(corpo.json()["items"])
    peitoral, triceps, ombro = itens[seed["Peitoral"]], itens[seed["Triceps"]], itens[seed["Ombro"]]
    assert peitoral["realized"]["direct_sets"]["value"] == pytest.approx(ESPERADO_PEITORAL)
    assert peitoral["realized"]["effective_sets_stimulus"]["value"] == pytest.approx(ESPERADO_PEITORAL)
    assert triceps["realized"]["indirect_sets"]["value"] == pytest.approx(ESPERADO_PEITORAL)
    assert triceps["realized"]["effective_sets_stimulus"]["value"] == pytest.approx(ESPERADO_PEITORAL * 0.5)
    assert ombro["realized"]["effective_sets_stimulus"]["value"] == pytest.approx(ESPERADO_PEITORAL * 0.25)
    # Séries FÍSICAS seguem contando linha (sem o aquecimento).
    assert peitoral["realized"]["physical_sets"]["value"] == 4
    (top,) = peitoral["top_exercises"]
    assert top["effective_sets_stimulus"] == pytest.approx(ESPERADO_PEITORAL)

    comparativo = next(
        g for g in corpo.json()["comparatives"]["by_session"] if g["muscle_group_id"] == seed["Peitoral"]
    )
    assert comparativo["points"][0]["effective_sets_stimulus"] == pytest.approx(ESPERADO_PEITORAL)

    # --- Drill-down ---------------------------------------------------------
    detalhe = client.get(f"/analytics/dashboard/muscles/{seed['Peitoral']}", params={"window": "28d"}).json()
    (sessao,) = detalhe["sessions"]
    assert sessao["effective_sets_stimulus"] == pytest.approx(ESPERADO_PEITORAL)

    # --- Overview de recuperação (v2, geração de ficha, Modo Treino) -------
    overview = _por_grupo(VolumeService().get_muscle_group_overview(days=14, limit=50, user_id=dono), "muscle_group")
    assert overview["Peitoral"]["direct_sets"] == pytest.approx(ESPERADO_PEITORAL)
    assert overview["Peitoral"]["stimulus_equivalent_sets"] == pytest.approx(ESPERADO_PEITORAL)
    assert overview["Triceps"]["stimulus_equivalent_sets"] == pytest.approx(ESPERADO_PEITORAL * 0.5)

    # --- Tendência semanal (alertas de volume baixo/excessivo) --------------
    semana = [
        linha
        for linha in VolumeService().get_rolling_weekly_trend_by_muscle_group(days=14, user_id=dono)
        if linha["period"] == "D-0 a D-6"
    ]
    assert _por_grupo(semana, "muscle_group")["Peitoral"]["stimulus_equivalent_sets"] == pytest.approx(
        ESPERADO_PEITORAL
    )

    # --- Janela de ativação diária (Fase 7) --------------------------------
    diaria = _por_grupo(VolumeService().get_daily_muscle_activation(days=7, user_id=dono), "muscle_group")
    assert diaria["Peitoral"]["sets_count"] == pytest.approx(ESPERADO_PEITORAL)

    # --- Aderência: completude só com trabalho; técnica X/N ----------------
    resposta = client.get(f"/analytics/dashboard/workouts/{seed['activity']}", params={"window": "28d"})
    assert resposta.status_code == 200, resposta.text
    sessao_detalhe = resposta.json()
    (item,) = sessao_detalhe["items"]
    assert item["performed"]["working_sets"] == 2
    assert item["performed"]["technique_sets"] == 2
    assert item["technique"]["performed_applications"] == 2
    assert item["technique"]["equivalent_sets"] == pytest.approx(1.0)
    assert sessao_detalhe["session"]["effective_sets_stimulus"]["value"] == pytest.approx(
        ESPERADO_PEITORAL * sum(PESOS.values())
    )


def test_recuperacao_igual_nos_dois_caminhos_de_leitura(seed, db_backend, monkeypatch):
    """A leitura agregada (empacotada) e a série a série continuam dando a
    MESMA dose - agora com o fator da técnica e sem o aquecimento."""
    congelar_relogio(monkeypatch)
    dono = seed["dono"]
    antes = MuscleRecoveryService().get_recovery_overview(days=14, limit=50, user_id=dono, aggregated_read=False)
    depois = MuscleRecoveryService().get_recovery_overview(days=14, limit=50, user_id=dono, aggregated_read=True)
    assert antes, "cenário vazio não provaria nada"
    for a, b in zip(sorted(antes, key=lambda r: r["muscle_group"]), sorted(depois, key=lambda r: r["muscle_group"])):
        assert a["muscle_group"] == b["muscle_group"]
        assert a["session_dose"] == pytest.approx(b["session_dose"])
        assert a["recovery_pct"] == b["recovery_pct"]
