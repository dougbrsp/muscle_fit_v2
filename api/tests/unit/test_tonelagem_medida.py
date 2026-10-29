"""Tonelagem / reps x kg só de séries de repetições (Lote T3 das medidas de
série): os predicados SQL usam `set_measures.sql_is_rep_based`, e as somas
inline de reps x kg passam por `sql_if_rep_based`.

Rodar: cd api && python -m pytest tests/unit/test_tonelagem_medida.py
"""

from __future__ import annotations

from app import load_conventions as lc
from app.domain import set_measures as sm
from app.intelligence.dashboard import exposure_service as es
from app.intelligence.dashboard import session_records as sr
from app.intelligence.report import daily_breakdown as db
from app.intelligence.report import markdown_report_service as mr

REP_BASED = sm.sql_is_rep_based("es.measure")


def test_predicado_da_tonelagem_exige_serie_de_reps():
    assert lc.sql_counts_in_tonnage("NULL") == REP_BASED
    com_convencao = lc.sql_counts_in_tonnage("eqex.load_convention")
    assert com_convencao.startswith("((eqex.load_convention IS NULL OR eqex.load_convention NOT IN (")
    assert com_convencao.endswith(f" AND {REP_BASED})")
    assert lc.sql_counts_in_tonnage("NULL", measure="s.measure") == sm.sql_is_rep_based("s.measure")


def test_soma_e_excluidas_usam_o_mesmo_predicado():
    assert REP_BASED in lc.sql_tonnage_sum("NULL")
    assert REP_BASED in lc.sql_tonnage_excluded_sets("NULL")


def test_sql_if_rep_based_e_duracao_do_dono():
    assert sm.sql_if_rep_based("x") == f"CASE WHEN {REP_BASED} THEN x ELSE 0 END"
    assert sm.sql_duration_seconds() == "CASE WHEN es.measure = 'duration' THEN es.duration_seconds END"


def test_recorde_e_relatorio_so_somam_series_de_reps():
    assert REP_BASED in sr.HISTORY_SQL_TEMPLATE and REP_BASED in sr.SETS_SQL
    assert sm.sql_if_rep_based(
        "COALESCE(es.reps, 0) * COALESCE(es.weight_kg, 0) * COALESCE(emc.stimulus_weight, 0)"
    ) in mr.TONNAGE_BY_GROUP_SQL_TEMPLATE
    assert REP_BASED in db.EQUIPMENT_DAILY_SQL_TEMPLATE and "duration_seconds_sum" in db.EQUIPMENT_DAILY_SQL_TEMPLATE


def test_exposicoes_leem_a_medida():
    for sql in (es.EXPOSURES_SQL_TEMPLATE, es.BATCH_EXPOSURES_SQL_TEMPLATE):
        assert "es.measure" in sql and "es.duration_seconds" in sql


def test_relatorio_por_equipamento_soma_os_segundos_da_prancha():
    linhas = [
        {"local_date": "2026-10-20", "exercise_row_id": 1, "exercise_id": 9, "exercise_name": "Prancha",
         "equipment_id": None, "equipment_name": None, "gym_name": None, "load_conv": None, "physical_sets": 3,
         "work_units": 3, "rpe_sum": 24, "rpe_n": 3, "working_sets": 3, "reps_sum": 0, "tonnage_kg": 0,
         "duration_seconds_sum": 175, "load_max": None, "load_sum": 0, "load_n": 0},
    ]
    saida = db.build_equipment(linhas, {})
    assert saida["days"][0]["duration_seconds"] == 175 and saida["period"][0]["duration_seconds"] == 175
