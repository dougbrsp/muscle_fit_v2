"""M63: o `volume_kg` GLOBAL usa a MESMA regra de tonelagem do M58.

Sem banco: `_fetch_all`/`fetch_all` e a deteccao da coluna da 045 sao
substituidos, e o teste confere o SQL emitido. A prova numerica contra MySQL
real (nos dois backends) esta em
`tests/integration/dataapi/test_dashboard_tonnage_load_convention.py`.

O que este arquivo prova:

- os helpers novos de `load_conventions` sao derivados de
  `sql_counts_in_tonnage` (uma regra so, nao uma segunda);
- `/analytics/summary`, `compute_training_trend`, `compute_rolling_weekly_trend`
  e `TrainingLoadService.get_weekly_summary` emitem esse predicado, os JOINs
  do par equipamento<->exercicio e a contagem `tonnage_excluded_sets`;
- sem a coluna da 045 (`NULL` = `total`) nada e excluido;
- o SQL continua UM statement por leitura, sem `%` solto que quebraria o
  `%s` dos parametros, e as series/reps/RPE continuam sem filtro.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app import analytics
from app.intelligence import training_load_service as tls
from app.load_conventions import (
    exercise_tonnage_joins_sql,
    sql_counts_in_tonnage,
    sql_tonnage_excluded_sets,
    sql_tonnage_sum,
)
from app.security import Identity

COLUNA = "eqex.load_convention"
USER_ID = 7
HOJE_DO_USUARIO = date(2026, 10, 7)


# ---------------------------------------------------------------------------
# Helpers de load_conventions
# ---------------------------------------------------------------------------


def test_soma_e_contagem_derivam_do_predicado_unico():
    predicado = sql_counts_in_tonnage(COLUNA)
    assert predicado in sql_tonnage_sum(COLUNA)
    assert f"NOT ({predicado})" in sql_tonnage_excluded_sets(COLUNA)
    assert "'assisted'" in predicado and "'bodyweight'" in predicado


def test_sem_coluna_da_045_nada_e_excluido():
    # Lote T3: sem a 045 a tonelagem so exclui serie que nao e de reps.
    rep_based = "(es.measure IS NULL OR es.measure = 'reps')"
    assert f"CASE WHEN {rep_based} THEN" in sql_tonnage_sum("NULL")
    assert f"NOT ({rep_based})" in sql_tonnage_excluded_sets("NULL")


def test_helpers_nao_tem_percent_solto():
    """Os fragmentos entram em SQL com `%s` de parametro: um `%` solto
    quebraria o PyMySQL e o shim da Data API do mesmo jeito."""
    for fragmento in (sql_tonnage_sum(COLUNA), sql_tonnage_excluded_sets(COLUNA), exercise_tonnage_joins_sql()):
        assert "%" not in fragmento


def test_joins_sao_left_e_pelo_equipamento_canonico():
    joins = exercise_tonnage_joins_sql()
    assert joins.count("LEFT JOIN") == 3
    assert "JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = e.mapping_id" in joins
    assert "JOIN gym_equipment ge ON ge.id = e.equipment_id" in joins
    assert "eqex.equipment_id = ge.equipment_id" in joins
    assert "eqex.is_active = 1" in joins


# ---------------------------------------------------------------------------
# SQL emitido pelas agregacoes globais
# ---------------------------------------------------------------------------


@pytest.fixture
def capturado(monkeypatch):
    sqls: list[tuple[str, tuple]] = []

    def _fake_fetch_all(sql, params=None):
        sqls.append((sql, params))
        return []

    monkeypatch.setattr(analytics, "_fetch_all", _fake_fetch_all)
    monkeypatch.setattr(analytics, "_load_convention_expr", lambda: COLUNA)
    # M76 Lote 2b: a janela parte do "hoje" do usuario, passado como parametro.
    monkeypatch.setattr(analytics, "_user_today", lambda user_id: HOJE_DO_USUARIO)
    return sqls


def _assert_volume_com_regra_m58(sql: str) -> None:
    assert f"{sql_tonnage_sum(COLUNA)} AS volume_kg" in sql
    assert f"{sql_tonnage_excluded_sets(COLUNA)} AS tonnage_excluded_sets" in sql
    assert exercise_tonnage_joins_sql().strip() in sql
    # Formula antiga (sem convencao) nao sobra em lugar nenhum.
    assert "COALESCE(SUM(COALESCE(es.reps, 0) * COALESCE(es.weight_kg, 0)), 0)" not in sql
    # Series, reps e RPE continuam contando TODAS as series.
    assert "COUNT(es.id) AS sets_count" in sql
    assert "COALESCE(SUM(COALESCE(es.reps, 0)), 0) AS reps_total" in sql
    assert "ROUND(AVG(es.rpe), 2) AS avg_rpe" in sql
    assert ";" not in sql  # um statement so (RDS Data API)


def test_summary_usa_regra_m58(capturado):
    analytics.analytics_summary(days=30, identity=Identity(user_id=USER_ID, role="regular", source="session"))
    sql, params = capturado[0]
    _assert_volume_com_regra_m58(sql)
    assert params == (HOJE_DO_USUARIO - timedelta(days=30), USER_ID)
    assert "CURDATE()" not in sql


@pytest.mark.parametrize("period", ["day", "week", "month", "year"])
def test_training_trend_usa_regra_m58(capturado, period):
    analytics.compute_training_trend(period, 365, user_id=USER_ID)
    sql, params = capturado[0]
    _assert_volume_com_regra_m58(sql)
    assert params == (HOJE_DO_USUARIO - timedelta(days=365), USER_ID)


def test_rolling_weekly_trend_usa_regra_m58(capturado):
    analytics.compute_rolling_weekly_trend(84, user_id=USER_ID)
    sql, params = capturado[0]
    _assert_volume_com_regra_m58(sql)
    assert params == (HOJE_DO_USUARIO, HOJE_DO_USUARIO - timedelta(days=84), USER_ID)


def test_rolling_weekly_trend_repassa_tonnage_excluded_sets(monkeypatch):
    monkeypatch.setattr(analytics, "_load_convention_expr", lambda: COLUNA)
    monkeypatch.setattr(analytics, "_user_today", lambda user_id: HOJE_DO_USUARIO)
    monkeypatch.setattr(
        analytics,
        "_fetch_all",
        lambda sql, params=None: [{"bucket_index": 0, "volume_kg": 2205.0, "tonnage_excluded_sets": 5}],
    )
    (linha,) = analytics.compute_rolling_weekly_trend(14, user_id=USER_ID)
    assert linha == {"period": "D-0 a D-6", "volume_kg": 2205.0, "tonnage_excluded_sets": 5}


# ---------------------------------------------------------------------------
# TrainingLoadService.get_weekly_summary (contexto da IA)
# ---------------------------------------------------------------------------


def test_weekly_summary_usa_regra_m58_e_declara_excluidas(monkeypatch):
    sqls: list[str] = []

    def _fake_fetch_all(self, sql, params=()):
        sqls.append(sql)
        if "total_volume_kg" in sql:
            return [{"completed_workouts": 4, "total_sets": 9, "total_volume_kg": 2205.0,
                     "tonnage_excluded_sets": 5, "avg_rpe": 8.2, "high_rpe_sets": 3}]
        return []

    monkeypatch.setattr(tls.TrainingLoadService, "fetch_all", _fake_fetch_all)
    monkeypatch.setattr(tls.TrainingLoadService, "_load_convention_expr", staticmethod(lambda: COLUNA))

    resumo = tls.TrainingLoadService().get_weekly_summary(user_id=USER_ID)

    (sql,) = [s for s in sqls if "total_volume_kg" in s]
    assert f"{sql_tonnage_sum(COLUNA)} AS total_volume_kg" in sql
    assert f"{sql_tonnage_excluded_sets(COLUNA)} AS tonnage_excluded_sets" in sql
    assert exercise_tonnage_joins_sql().strip() in sql
    assert "COUNT(es.id) AS total_sets" in sql
    assert resumo["total_volume_kg"] == 2205.0
    assert resumo["tonnage_excluded_sets"] == 5
    assert resumo["total_sets"] == 9


def test_weekly_summary_sem_linha_devolve_zero_excluidas(monkeypatch):
    monkeypatch.setattr(tls.TrainingLoadService, "fetch_all", lambda self, sql, params=(): [])
    monkeypatch.setattr(tls.TrainingLoadService, "_load_convention_expr", staticmethod(lambda: "NULL"))
    resumo = tls.TrainingLoadService().get_weekly_summary(user_id=USER_ID)
    assert resumo["tonnage_excluded_sets"] == 0
    assert resumo["total_volume_kg"] == 0.0
