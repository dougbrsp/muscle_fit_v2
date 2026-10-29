"""Edicao manual de FC de repouso / HRV (069) contra MySQL real, nos DOIS
backends (PyMySQL de dev e shim da RDS Data API de producao).

O que este arquivo prova:
- a migracao 069 esta no schema de teste (colunas com default 'intervals');
- precedencia do sync (`intervals_store.upsert_wellness`), linha a linha:
  Intervals com valor sobrescreve a edicao; sem valor mantem a edicao manual;
  sem valor e origem 'intervals' grava NULL (como antes); edicao limpa volta
  a ser preenchida;
- ARMADILHA da ordem no `ON DUPLICATE KEY UPDATE`: o MESMO resultado com as
  atribuicoes (valor, origem) trocadas de ordem, no MySQL de verdade;
- PATCH cria a linha inexistente so com o campo enviado, nao mexe em
  `synced_at` de linha existente, e nunca toca a linha de outro usuario;
- leitura do /dashboard e do GET /wellness expoe a origem.

Sem rede: o Intervals.icu nunca e chamado (o upsert recebe o payload).
"""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Importados no topo de proposito: `use_backend` so troca o backend de modulos
# `app.*` JA importados (ver tests/support/dataapi_backend.py).
from app import intervals_integration, intervals_store  # noqa: F401
from app.db import get_conn
from app.intelligence.dashboard import time_windows  # noqa: F401
from app.security import Identity, get_current_identity
from tests.support.grupo_b_schema import ensure_grupo_b_tables
from tests.support.isolamento_v3 import truncar
from tests.support.relogio_usuario import hoje_do_usuario

HOJE = hoje_do_usuario()
DIA = HOJE - _dt.timedelta(days=1)


@pytest.fixture
def seed(raw):
    ensure_grupo_b_tables(raw)
    truncar(raw)
    ids: dict[str, int] = {}
    with raw.cursor() as cur:
        for nome in ("dono", "outro"):
            cur.execute(
                "INSERT INTO users (username, password_hash, role, is_active) VALUES (%s, 'x', 'regular', 1)",
                (f"wellness-manual-{nome}",),
            )
            ids[nome] = cur.lastrowid
    raw.commit()
    ids["raw"] = raw  # type: ignore[assignment]
    return ids


def _linha(raw, user_id: int, dia: _dt.date = DIA) -> dict | None:
    raw.commit()  # enxerga o que o backend gravou
    with raw.cursor() as cur:
        cur.execute(
            "SELECT resting_hr_bpm, resting_hr_source, hrv_ms, hrv_source, manual_updated_at, synced_at, fitness_ctl "
            "FROM intervals_wellness WHERE user_id = %s AND wellness_date = %s",
            (user_id, dia),
        )
        return cur.fetchone()


def _valores(row) -> tuple:
    def f(v):
        return None if v is None else float(v)

    return (f(row["resting_hr_bpm"]), row["resting_hr_source"], f(row["hrv_ms"]), row["hrv_source"])


def _sync(user_id: int, resting_hr=None, hrv=None, dia: _dt.date = DIA) -> None:
    item = {"id": dia.isoformat(), "ctl": 50, "atl": 40}
    if resting_hr is not None:
        item["restingHR"] = resting_hr
    if hrv is not None:
        item["hrv"] = hrv
    with get_conn() as conn:
        intervals_store.upsert_wellness(conn, user_id, [item])


def _manual(user_id: int, values: dict, dia: _dt.date = DIA) -> None:
    with get_conn() as conn:
        intervals_store.set_manual_wellness(conn, user_id, dia.isoformat(), values)


def _client(user_id: int) -> TestClient:
    app = FastAPI()
    app.include_router(intervals_integration.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=user_id, role="regular", source="session")
    return TestClient(app, raise_server_exceptions=False)


def _swapped_sql() -> str:
    """O SQL do upsert com cada par (valor, origem) em ordem TROCADA."""
    sql = intervals_store._UPSERT_WELLNESS_SQL
    for value_column, source_column in intervals_store.MANUAL_WELLNESS_SOURCE_COLUMNS.items():
        original = intervals_store._sync_precedence_assignments(value_column, source_column)
        value_part, source_part = original.split(f", {source_column} = ", 1)
        swapped = f"{source_column} = {source_part}, {value_part}"
        assert original in sql
        sql = sql.replace(original, swapped)
    return sql


@pytest.fixture(params=["ordem-do-codigo", "ordem-trocada"])
def ordem(request, monkeypatch):
    if request.param == "ordem-trocada":
        monkeypatch.setattr(intervals_store, "_UPSERT_WELLNESS_SQL", _swapped_sql())
    return request.param


def test_069_colunas_novas_com_default_intervals(seed, db_backend):
    _sync(seed["dono"], resting_hr=55, hrv=60)
    row = _linha(seed["raw"], seed["dono"])
    assert _valores(row) == (55.0, "intervals", 60.0, "intervals")
    assert row["manual_updated_at"] is None


def test_precedencia_do_sync(seed, db_backend, ordem):
    dono = seed["dono"]
    raw = seed["raw"]

    # edicao manual cria a linha so com o campo enviado
    _manual(dono, {"resting_hr_bpm": 48.0, "hrv_ms": 70.0})
    row = _linha(raw, dono)
    assert _valores(row) == (48.0, "manual", 70.0, "manual"), (db_backend, ordem)
    assert row["fitness_ctl"] is None and row["manual_updated_at"] is not None

    # Intervals sem o dado do dia: a edicao fica
    _sync(dono)
    assert _valores(_linha(raw, dono)) == (48.0, "manual", 70.0, "manual"), (db_backend, ordem)
    assert _linha(raw, dono)["fitness_ctl"] == Decimal("50.00")

    # Intervals com o dado de UMA metrica: so ela e sobrescrita
    _sync(dono, hrv=65)
    assert _valores(_linha(raw, dono)) == (48.0, "manual", 65.0, "intervals"), (db_backend, ordem)

    # e com a outra
    _sync(dono, resting_hr=52, hrv=65)
    assert _valores(_linha(raw, dono)) == (52.0, "intervals", 65.0, "intervals"), (db_backend, ordem)

    # origem 'intervals' e Intervals sem valor: grava NULL, como antes da 069
    _sync(dono)
    assert _valores(_linha(raw, dono)) == (None, "intervals", None, "intervals"), (db_backend, ordem)


def test_limpar_edicao_e_o_sync_preenche_de_novo(seed, db_backend, ordem):
    dono, raw = seed["dono"], seed["raw"]
    _manual(dono, {"hrv_ms": 70.0})
    _manual(dono, {"hrv_ms": None})
    assert _valores(_linha(raw, dono)) == (None, "intervals", None, "intervals")
    _sync(dono, hrv=61)
    assert _valores(_linha(raw, dono)) == (None, "intervals", 61.0, "intervals"), (db_backend, ordem)


def test_so_limpar_sem_linha_nao_cria_linha(seed, db_backend):
    _manual(seed["dono"], {"resting_hr_bpm": None})
    assert _linha(seed["raw"], seed["dono"]) is None


def test_edicao_em_linha_existente_nao_mexe_em_synced_at(seed, db_backend):
    dono, raw = seed["dono"], seed["raw"]
    _sync(dono, resting_hr=55)
    with raw.cursor() as cur:
        cur.execute(
            "UPDATE intervals_wellness SET synced_at = '2026-01-01 10:00:00' WHERE user_id = %s AND wellness_date = %s",
            (dono, DIA),
        )
    raw.commit()
    _manual(dono, {"resting_hr_bpm": 47.0})
    row = _linha(raw, dono)
    assert _valores(row)[:2] == (47.0, "manual")
    assert row["synced_at"] == _dt.datetime(2026, 1, 1, 10, 0, 0), db_backend


def test_patch_isolado_por_usuario_e_leitura_expoe_origem(seed, db_backend):
    dono, outro, raw = seed["dono"], seed["outro"], seed["raw"]
    _sync(outro, resting_hr=60, hrv=50)

    response = _client(dono).patch(f"/integrations/intervals/wellness/{DIA.isoformat()}", json={"resting_hr_bpm": 49})
    assert response.status_code == 200, response.text
    assert response.json()["item"]["resting_hr_source"] == "manual"

    assert _valores(_linha(raw, dono)) == (49.0, "manual", None, "intervals")
    assert _valores(_linha(raw, outro)) == (60.0, "intervals", 50.0, "intervals"), "linha de outro usuario intocada"

    futuro = (HOJE + _dt.timedelta(days=1)).isoformat()
    assert _client(dono).patch(f"/integrations/intervals/wellness/{futuro}", json={"hrv_ms": 60}).status_code == 422
    assert _client(dono).patch(f"/integrations/intervals/wellness/{DIA.isoformat()}", json={"hrv_ms": 900}).status_code == 422

    trend = intervals_integration._cached_wellness_trend(dono, 30)
    ponto = next(p for p in trend if p["date"] == DIA.isoformat())
    assert (ponto["restingHr"], ponto["restingHrSource"], ponto["hrv"], ponto["hrvSource"]) == (49.0, "manual", None, "intervals")

    items = _client(dono).get("/integrations/intervals/wellness?days=30").json()["items"]
    assert [(i["resting_hr_source"], i["hrv_source"]) for i in items] == [("manual", "intervals")], db_backend
