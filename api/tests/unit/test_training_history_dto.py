"""`domain/training_history.py`: agrupamento das linhas planas da query em
DTOs (funcao PURA) e o SQL que a query usa (PR-10, secao 5.2).

O teste de integracao contra MySQL real esta em
tests/integration/dataapi/test_pr10_progressao_shadow.py.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from app.domain import training_history as th
from app.set_types import DROP_SET, WORKING


def _row(**overrides):
    base = {
        "activity_id": 1,
        "local_date": date(2026, 9, 1),
        "started_at": datetime(2026, 9, 1, 17, 0),
        "ended_at": datetime(2026, 9, 1, 18, 0),
        "workout_plan_id": 77,
        "exercise_row_id": 10,
        "sequence": 1,
        "exercise_id": 1001,
        "equipment_id": 500,
        "gym_equipment_id": 900,
        "workout_plan_exercise_id": 300,
        "exercise_muscle_pain": None,
        "exercise_joint_pain": 0,
        "prescribed_sets_count": 3,
        "prescribed_reps_target": "10-12",
        "prescribed_load_target": "40",
        "prescribed_rpe_target": "9",
        "prescribed_technique": "rest_pause",
        "preparatory_sets_count": 2,
        "set_row_id": 1,
        "set_number": 1,
        "set_type": "working",
        "reps": 12,
        "weight_kg": Decimal("40.00"),
        "rpe": Decimal("9.00"),
        "set_muscle_pain": None,
        "set_joint_pain": None,
    }
    return {**base, **overrides}


def test_agrupa_sessao_exercicio_e_series():
    rows = [
        _row(set_row_id=1, set_number=1),
        _row(set_row_id=2, set_number=2, reps=11),
        _row(set_row_id=3, set_number=3, exercise_row_id=11, sequence=2, exercise_id=1002),
    ]
    sessions = th.build_sessions(rows)
    assert len(sessions) == 1
    session = sessions[0]
    assert session.activity_id == 1 and session.workout_plan_id == 77
    assert session.occurred_at == datetime(2026, 9, 1, 18, 0)
    assert [item.exercise_id for item in session.exercises] == [1001, 1002]
    assert [item.reps for item in session.exercises[0].sets] == [12, 11]


def test_prescricao_vem_de_workout_plan_exercises():
    """`prescribed{sets, reps_min, reps_max, load, rpe_target, technique}`."""
    execution = th.build_sessions([_row()])[0].exercises[0]
    assert execution.prescribed.to_dict() == {
        "sets": 3,
        "reps_min": 10,
        "reps_max": 12,
        "load": 40.0,
        "rpe_target": 9.0,
        "rpe_targets": [9.0],
        "technique": "rest_pause",
    }  # linha sem snapshot (ficha legada): sem a chave `frozen`
    # `prescription_snapshot_id` e o id do item da ficha (o JSON congelado vem
    # no PR-11, que cria `workout_plan_exercises.prescription_json`)
    assert execution.prescription_snapshot_id == 300


def test_treino_sem_plano_fica_sem_prescricao_mas_mantem_o_par():
    rows = [
        _row(
            workout_plan_exercise_id=None,
            prescribed_sets_count=None,
            prescribed_reps_target=None,
            prescribed_load_target=None,
            prescribed_rpe_target=None,
            prescribed_technique=None,
        )
    ]
    execution = th.build_sessions(rows)[0].exercises[0]
    assert execution.prescribed.sets is None and execution.prescribed.reps_max is None
    assert execution.pair == (1001, 500)


def test_par_sem_exercicio_ou_equipamento_nao_resolve():
    assert th.build_sessions([_row(exercise_id=None)])[0].exercises[0].pair is None
    assert th.build_sessions([_row(equipment_id=None)])[0].exercises[0].pair is None


def test_exercicio_sem_serie_nao_preparatoria_aparece_com_zero_series():
    """O LEFT JOIN preserva a linha do exercicio quando todas as series foram
    filtradas (so aquecimento/ajuste)."""
    execution = th.build_sessions([_row(set_row_id=None, set_number=None, preparatory_sets_count=3)])[0].exercises[0]
    assert execution.sets == () and execution.preparatory_sets_count == 3


def test_set_type_sai_normalizado_do_dto():
    """O backfill de `dropset` nunca foi feito: a normalizacao e na LEITURA."""
    execution = th.build_sessions([_row(set_type="dropset")])[0].exercises[0]
    assert execution.sets[0].set_type == DROP_SET


def test_set_type_nulo_e_working_por_default_historico():
    execution = th.build_sessions([_row(set_type=None)])[0].exercises[0]
    assert execution.sets[0].set_type == WORKING


def test_dor_nula_fica_nula_e_zero_fica_falso():
    """D5: NULL = nao reportado; 0 = reportado sem dor."""
    nulo = th.build_sessions([_row(set_joint_pain=None, exercise_joint_pain=None)])[0].exercises[0]
    zero = th.build_sessions([_row(set_joint_pain=0, exercise_joint_pain=0)])[0].exercises[0]
    assert nulo.sets[0].joint_pain is None and nulo.joint_pain is None
    assert zero.sets[0].joint_pain is False and zero.joint_pain is False


def test_varias_sessoes_saem_separadas():
    rows = [_row(activity_id=1), _row(activity_id=2, exercise_row_id=20, local_date=date(2026, 9, 9))]
    sessions = th.build_sessions(rows)
    assert [item.activity_id for item in sessions] == [1, 2]


@pytest.mark.parametrize(
    "valor, esperado",
    [("10-12", (10, 12)), ("8 - 10", (8, 10)), ("12", (12, 12)), ("", (None, None)), (None, (None, None)), ("AMRAP", (None, None))],
)
def test_parse_reps_range(valor, esperado):
    assert th.parse_reps_range(valor) == esperado


@pytest.mark.parametrize("valor, esperado", [("RPE 9", 9), ("9", 9), (9.0, 9), (None, None), ("", None), (True, None)])
def test_parse_int(valor, esperado):
    assert th.parse_int(valor) == esperado


@pytest.mark.parametrize(
    "valor, esperado", [("40 kg", Decimal("40")), ("37,5", Decimal("37.5")), (40, Decimal("40")), (None, None), ("", None)]
)
def test_parse_decimal(valor, esperado):
    assert th.parse_decimal(valor) == esperado


# ---------------------------------------------------------------------------
# O SQL
# ---------------------------------------------------------------------------


def test_sql_usa_os_predicados_de_set_types_e_nao_literais():
    """Regra do PR-10: `set_type` em SQL novo SO pelos helpers de
    `app/set_types.py` (o `dropset` legado nunca foi normalizado no banco)."""
    sql = th.SESSIONS_SQL
    assert "COALESCE(LOWER(TRIM(es.set_type)), 'working')" in sql
    assert "'warmup'" in sql and "'feeler'" in sql  # vem de sql_not_preparatory()
    assert "COALESCE(LOWER(TRIM(es.set_type)), 'working') IN ('', 'working')" in th.WORKING_SETS_COUNT_SQL
    # nenhuma comparacao crua com a coluna, que deixaria `dropset` passar
    assert "es.set_type =" not in sql and "es.set_type IN" not in sql


def test_sql_le_so_sessoes_concluidas_e_com_series():
    sql = th.SESSIONS_SQL
    assert "a.session_status = 'done'" in sql
    # historico so com agregados (sem exercise_sets) e ignorado (secao 5.10)
    assert "EXISTS (" in sql and "JOIN exercise_sets exs ON exs.exercise_id = ex.id" in sql


def test_sql_usa_a_chave_canonica_do_par():
    sql = th.SESSIONS_SQL
    # exercicio canonico = exercise_catalog.id via exercises.mapping_id
    assert "COALESCE(e.mapping_id, wpe.mapping_id) AS exercise_id" in sql
    # equipamento canonico = equipment.id via gym_equipment (nunca gym_equipment.id)
    assert "ge.equipment_id             AS equipment_id" in sql
    assert "LEFT JOIN gym_equipment ge ON ge.id = e.equipment_id" in sql


def test_sql_e_uma_unica_query_sem_n_mais_1():
    sql = th.SESSIONS_SQL
    # as N sessoes saem de UMA subconsulta com LIMIT; o resto vem por JOIN
    assert sql.count("LIMIT %s") == 1
    assert "ORDER BY s.occurred_at ASC" in sql
    assert ";" not in sql


def test_ordem_cronologica_crescente_da_mais_antiga_para_a_mais_nova():
    assert "ORDER BY s.occurred_at ASC, s.activity_id ASC" in th.SESSIONS_SQL


def test_janela_default_e_21_sessoes():
    assert th.DEFAULT_SESSION_WINDOW == 21


# ---------------------------------------------------------------------------
# Lote 2/P7: ficha congelada lida do `prescription_json` (colunas `frozen_*`)
# ---------------------------------------------------------------------------


def test_ficha_congelada_vem_das_colunas_frozen_como_o_json_unquote_devolve():
    """`JSON_UNQUOTE(JSON_EXTRACT(...))` devolve TEXTO: numero como "3",
    booleano como "false" e o JSON null como "null"."""
    row = _row(
        frozen_schema_version="1",
        frozen_sets_count="2",
        frozen_effective_rpe_target="9",
        frozen_effective_rpe_ceiling="9",
        frozen_failure_allowed="false",
        frozen_decision="HOLD",
    )
    prescribed = th.build_sessions([row])[0].exercises[0].prescribed
    frozen = prescribed.frozen
    assert frozen == th.FrozenPrescription(
        sets_count=2, effective_rpe_target=9, effective_rpe_ceiling=9, failure_allowed=False, decision="HOLD"
    )
    assert prescribed.to_dict()["frozen"] == frozen.to_dict()
    assert frozen.intensity_snapshot() == {
        "effective_rpe_target": 9,
        "effective_rpe_ceiling": 9,
        "failure_allowed": False,
        "failure_denied_by": None,
        "safety_constraints": {"max_safe_rpe": None},
    }


def test_ficha_congelada_leva_a_origem_da_negacao_da_falha():
    """RPE 10 Lote 1/P1: a origem congelada (ou o teto do par, para derivar em
    snapshot antigo) chega ao snapshot de intensidade da avaliacao."""
    row = _row(
        frozen_schema_version="1",
        frozen_effective_rpe_ceiling="9",
        frozen_failure_allowed="false",
        frozen_failure_denied_by="RECOVERY",
        frozen_pair_max_safe_rpe="10",
    )
    frozen = th.build_sessions([row])[0].exercises[0].prescribed.frozen
    assert (frozen.failure_denied_by, frozen.pair_max_safe_rpe) == ("RECOVERY", 10)
    snap = frozen.intensity_snapshot()
    assert snap["failure_denied_by"] == "RECOVERY"
    assert snap["safety_constraints"] == {"max_safe_rpe": 10}
    # snapshot antigo: sem a origem, mas com o teto do par -> derivavel
    antigo = th.FrozenPrescription.from_row(
        th.frozen_columns_from_snapshot(
            {"effective_rpe_ceiling": 9, "failure_allowed": False, "safety_constraints": {"max_safe_rpe": 10}}
        )
    )
    assert (antigo.failure_denied_by, antigo.pair_max_safe_rpe) == (None, 10)


def test_ficha_congelada_null_ou_ausente_e_tratada_como_sem_snapshot():
    assert th.build_sessions([_row()])[0].exercises[0].prescribed.frozen is None
    nulos = {column: "null" for column in th.FROZEN_SNAPSHOT_KEYS}
    assert th.build_sessions([_row(**nulos)])[0].exercises[0].prescribed.frozen is None
    so_versao = _row(frozen_schema_version="1")
    assert th.build_sessions([so_versao])[0].exercises[0].prescribed.frozen is None


def test_ficha_congelada_skip_e_decisao_null_tolerados():
    row = _row(
        frozen_schema_version="1",
        frozen_sets_count="3",
        frozen_effective_rpe_target="9",
        frozen_effective_rpe_ceiling="9",
        frozen_failure_allowed="true",
        frozen_decision="SKIP",
    )
    frozen = th.build_sessions([row])[0].exercises[0].prescribed.frozen
    assert frozen.decision == "SKIP" and frozen.failure_allowed is True
    legado = _row(frozen_schema_version="1", frozen_sets_count="3", frozen_decision="null")
    assert th.build_sessions([legado])[0].exercises[0].prescribed.frozen.decision is None


def test_colunas_frozen_de_um_snapshot_espelham_o_sql():
    snapshot = {
        "schema_version": 1,
        "sets_count": 3,
        "effective_rpe_target": 8,
        "effective_rpe_ceiling": 9,
        "failure_allowed": False,
        "decision": "INCREASE_LOAD",
        "clamps": [{"field": "rpe_target"}],
    }
    columns = th.frozen_columns_from_snapshot(snapshot)
    assert set(columns) == set(th.FROZEN_SNAPSHOT_KEYS)
    frozen = th.FrozenPrescription.from_row(columns)
    assert (frozen.sets_count, frozen.effective_rpe_target, frozen.effective_rpe_ceiling) == (3, 8, 9)
    assert frozen.failure_allowed is False and frozen.decision == "INCREASE_LOAD"
    assert th.FrozenPrescription.from_row(th.frozen_columns_from_snapshot(None)) is None


def test_sql_le_as_chaves_do_snapshot_sem_trazer_o_json_inteiro():
    for column, key in th.FROZEN_SNAPSHOT_KEYS.items():
        assert f"'$.{key}'" in th.SESSIONS_SQL and f"AS {column}" in th.SESSIONS_SQL
    assert "wpe.prescription_json  " not in th.SESSIONS_SQL
    assert "wpe.prescription_json AS" not in th.SESSIONS_SQL
