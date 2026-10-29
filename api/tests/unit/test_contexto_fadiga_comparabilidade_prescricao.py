"""PR 2 da fadiga previa: o contexto de fadiga (dono `domain/prior_fatigue.py`)
entra na chave de comparabilidade e na trilha da progressao.

Casos do pedido (2026-10-07):
- supino 1o na segunda (descansado, 3x10x30) e 5o na quinta (pre-fatigado,
  6x20) nao e regressao;
- exposicoes de outro contexto intercaladas nao interrompem a sequencia;
- equipamento diferente continua NOT_COMPARABLE / interrompendo;
- primeira exposicao num contexto novo nao dispara regressao/deload;
- recorde continua absoluto;
- prescricao por contexto (com fallback para a ultima carga de qualquer
  contexto, sem desconto).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from app.domain.progression_engine import track_key
from app.domain import progression_engine as pe
from app.domain import progression_evaluator as ev
from app.domain.prior_fatigue import MuscleContribution, PriorFatigueBand
from app.domain.session_fatigue import ExecutedExercise, planned_contexts, session_contexts
from app.domain.training_history import PrescribedSpec
from app.generation import deterministic_prescription as dp
from app.intelligence.dashboard import exposure_service as es
from app.intelligence.dashboard.comparability import (
    MISMATCH_FATIGUE_CONTEXT,
    REASON_FIRST_IN_CONTEXT,
    REASON_OTHER_CONTEXT_SKIPPED,
    ExposureSummary,
    WorkingSet,
    build_key,
    classify_exercise_progression,
    mismatch_with_current,
    physical_keys_match,
    trailing_comparable_run,
)
from app.intelligence.dashboard.metrics_registry import ExerciseProgressionStatus as S
from tests.support import progression_fixtures as fx

RESTED, PRE, HEAVY = (band.value for band in PriorFatigueBand)
SUPINO, CRUCIFIXO, TRICEPS = 10, 11, 12
BANCO, POLIA = 7, 8
PEITO, TRICEPS_G, OMBRO = 1, 2, 3

CONTRIB = {
    SUPINO: (
        MuscleContribution(PEITO, "Peitoral", "PRIMARY", 1.0),
        MuscleContribution(TRICEPS_G, "Triceps", "SECONDARY", 0.5),
        MuscleContribution(OMBRO, "Deltoide", "SECONDARY", 0.5),
    ),
    CRUCIFIXO: (MuscleContribution(PEITO, "Peitoral", "PRIMARY", 1.0),),
    TRICEPS: (MuscleContribution(TRICEPS_G, "Triceps", "PRIMARY", 1.0),),
}


def _exposure(context, *, load, reps, sets=3, rpe=9.0, equipment=BANCO):
    """Exposicao de supino: `sets` series de `reps` na carga `load`."""
    from app.domain import e1rm

    value = e1rm.estimated_1rm(load, reps, rpe)
    return ExposureSummary(
        key=build_key(SUPINO, equipment, "total", fatigue_context=context),
        total_reps=reps * sets,
        best_load_kg=float(load),
        avg_rpe=rpe,
        e1rm=None if value is None else float(value),
        e1rm_reps=reps,
        sets=tuple(WorkingSet(reps=reps, weight_kg=float(load)) for _ in range(sets)),
    )


# ---------------------------------------------------------------------------
# Comparabilidade
# ---------------------------------------------------------------------------


def test_supino_descansado_x_pre_fatigado_nao_e_regressao():
    segunda = _exposure(RESTED, load=30, reps=10)
    quinta = _exposure(PRE, load=20, reps=6)
    status, reasons = classify_exercise_progression([segunda, quinta])
    assert status is S.INSUFFICIENT_DATA
    assert REASON_FIRST_IN_CONTEXT in reasons

    # sem contexto (comportamento anterior) a mesma dupla era "regressao"
    sem_contexto = [replace(item, key=item.key.physical) for item in (segunda, quinta)]
    assert classify_exercise_progression(sem_contexto)[0] is S.REGRESSING


def test_outro_contexto_intercalado_nao_interrompe_a_sequencia():
    historico = [
        _exposure(RESTED, load=30, reps=10),
        _exposure(PRE, load=20, reps=6),
        _exposure(RESTED, load=30, reps=11),
        _exposure(PRE, load=20, reps=5),
        _exposure(RESTED, load=30, reps=12),
    ]
    run = trailing_comparable_run(historico)
    assert [item.sets[0].reps for item in run] == [10, 11, 12]
    status, reasons = classify_exercise_progression(historico)
    assert status is S.PROGRESSING
    assert REASON_OTHER_CONTEXT_SKIPPED in reasons


def test_contexto_atual_compara_so_com_o_proprio_contexto():
    historico = [
        _exposure(PRE, load=20, reps=8),
        _exposure(RESTED, load=30, reps=10),
        _exposure(PRE, load=20, reps=6),
    ]
    status, _ = classify_exercise_progression(historico)
    assert status is S.REGRESSING  # 8 -> 6 reps na mesma carga, ambos pre-fatigados


def test_equipamento_diferente_continua_nao_comparavel():
    historico = [_exposure(RESTED, load=30, reps=10, equipment=POLIA), _exposure(RESTED, load=30, reps=10)]
    assert classify_exercise_progression(historico)[0] is S.NOT_COMPARABLE


def test_troca_de_equipamento_interrompe_mesmo_com_contexto_pulado():
    historico = [
        _exposure(RESTED, load=30, reps=10),
        _exposure(RESTED, load=30, reps=10, equipment=POLIA),
        _exposure(PRE, load=20, reps=6),
        _exposure(RESTED, load=30, reps=12),
    ]
    assert len(trailing_comparable_run(historico)) == 1
    assert classify_exercise_progression(historico)[0] is S.INSUFFICIENT_DATA


def test_motivo_de_nao_comparavel_por_contexto():
    atual = build_key(SUPINO, BANCO, "total", fatigue_context=RESTED)
    outra = build_key(SUPINO, BANCO, "total", fatigue_context=PRE)
    assert physical_keys_match(atual, outra)
    assert mismatch_with_current(outra, atual) == MISMATCH_FATIGUE_CONTEXT
    assert mismatch_with_current(build_key(SUPINO, POLIA, "total", fatigue_context=RESTED), atual) == "equipment"


def _raw(row_id, activity_id, load, reps, day):
    return {
        "exercise_row_id": row_id,
        "activity_id": activity_id,
        "local_date": day,
        "started_at": None,
        "position_in_session": 1,
        "equipment_id": BANCO,
        "equipment_name": "Banco",
        "load_convention_raw": "total",
        "prescribed_reps_min": None,
        "prescribed_reps_max": None,
        "sets": [{"set_number": n, "reps": reps, "weight_kg": load, "rpe": 9.0} for n in (1, 2, 3)],
    }


def test_exposicao_leva_contexto_e_rotulo_do_dono_e_recorde_e_absoluto():
    from app.domain.prior_fatigue import PriorFatigue

    raws = [_raw(1, 100, 30.0, 10, "2026-10-05"), _raw(2, 101, 20.0, 6, "2026-10-08")]
    fadiga = {
        1: None,
        2: PriorFatigue(PEITO, "Peitoral", 4.0, 4.0 / 9, PriorFatigueBand.PRE_FATIGUED),
    }
    exposures = es.assemble_exposures(raws, SUPINO, fadiga)
    assert [item["fatigue_context"]["band"] for item in exposures] == [RESTED, PRE]
    assert exposures[1]["fatigue_context"]["band_label"] == "pré-fatigado"
    assert exposures[1]["comparability_key"]["fatigue_context"] == PRE
    # recorde absoluto: o melhor e o de 30 kg (outro contexto), nao o de 20 kg
    records = es._records(exposures, exposures[-1]["_key"])
    assert records["best_load_kg"]["value"] == 30.0
    # sem medicao de contexto, a chave fica sem contexto (comportamento anterior)
    assert es.assemble_exposures(raws, SUPINO)[0]["_key"].fatigue_context is None


# ---------------------------------------------------------------------------
# Montagem da sessao (entradas do dono)
# ---------------------------------------------------------------------------


def _working(n):
    return tuple({"set_number": i, "set_type": "working", "reps": 10, "rpe": 9} for i in range(1, n + 1))


def test_contexto_da_sessao_real_segue_a_ordem_e_os_pesos():
    segunda = [ExecutedExercise(1, 1, SUPINO, _working(3)), ExecutedExercise(2, 2, CRUCIFIXO, _working(3))]
    quinta = [
        ExecutedExercise(3, 1, CRUCIFIXO, _working(3)),
        ExecutedExercise(4, 2, TRICEPS, _working(3)),
        ExecutedExercise(5, 3, SUPINO, _working(6)),
    ]
    assert session_contexts(segunda, CONTRIB)[1] is PriorFatigueBand.RESTED
    # supino depois de 3 series de crucifixo (peitoral x 1,0 = 3): pre-fatigado
    assert session_contexts(quinta, CONTRIB)[5] is PriorFatigueBand.PRE_FATIGUED
    # grupos irmaos nao se somam: triceps antes do supino nao conta no peitoral
    so_triceps = [ExecutedExercise(6, 1, TRICEPS, _working(6)), ExecutedExercise(7, 2, SUPINO, _working(3))]
    assert session_contexts(so_triceps, CONTRIB)[7] is PriorFatigueBand.RESTED


def test_contexto_planejado_usa_a_posicao_no_slot():
    bands = planned_contexts([(3.0, CRUCIFIXO), (3.0, CRUCIFIXO), (3.0, SUPINO)], CONTRIB)
    assert bands == [PriorFatigueBand.RESTED, PriorFatigueBand.PRE_FATIGUED, PriorFatigueBand.HEAVILY_FATIGUED]


# ---------------------------------------------------------------------------
# Progressao: trilha por contexto
# ---------------------------------------------------------------------------


def _exec(row_id, *, reps, weight, sequence=1):
    base = fx.execution(
        exercise_id=SUPINO,
        equipment_id=BANCO,
        sequence=sequence,
        set_list=fx.sets(reps=reps, rpe=9, weight=weight, count=3),
        prescribed=PrescribedSpec(sets=3, reps_min=10, reps_max=12, rpe_target=10),
    )
    return replace(base, exercise_row_id=row_id)


def _inputs(contexts):
    track = fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO)
    return replace(fx.inputs(tracks=[track]), fatigue_context=contexts)


def test_execucao_em_contexto_novo_cria_trilha_propria_em_calibracao_e_neutra():
    descansada = fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO, load=30)
    groups, tracks = fx.state_maps([descansada], [fx.group(muscle_group_id=PEITO, phase=pe.F2)])
    sessao = fx.session(activity_id=2, day="2026-10-08", executions=[_exec(20, reps=6, weight=20)])
    result = ev.evaluate_session(sessao, [sessao], group_states=groups, tracks=tracks, inputs=_inputs({20: PRE}))
    nova = result.tracks[track_key(SUPINO, BANCO, PRE)]
    assert nova.working_load_kg == 20 and nova.calibrated_at is not None
    assert result.tracks[track_key(SUPINO, BANCO, RESTED)].working_load_kg == 30  # intacta
    assert result.track_outcomes[pe.track_key(SUPINO, BANCO, PRE)].outcome == pe.NEUTRAL


def test_primeira_exposicao_no_contexto_nao_conta_para_regressao():
    """Pre-fatigado abaixo de reps_min em P1 (calibracao) e P2: ainda nao e
    D10 - a calibracao nao entra no historico. Em P3 (2a e 3a contam) e."""
    inputs = _inputs({21: PRE, 22: PRE, 23: PRE})
    sessions = [
        fx.session(activity_id=1, day="2026-10-01", executions=[_exec(21, reps=6, weight=20)]),
        fx.session(activity_id=2, day="2026-10-08", executions=[_exec(22, reps=6, weight=20)]),
        fx.session(activity_id=3, day="2026-10-15", executions=[_exec(23, reps=6, weight=20)]),
    ]
    group_states, tracks = fx.state_maps([], [fx.group(muscle_group_id=PEITO, phase=pe.F2)])
    actions = []
    for index, session in enumerate(sessions):
        result = ev.evaluate_session(
            session, sessions[: index + 1], group_states=group_states, tracks=tracks, inputs=inputs
        )
        group_states, tracks = result.group_states, result.tracks
        actions.append(group_states[PEITO].pending_action)
    assert actions[:2] == [None, None]
    assert actions[2] == pe.DECREASE_LOAD
    assert group_states[PEITO].pending_payload["track_key"] == [SUPINO, BANCO, PRE]


def test_resolve_track_por_contexto_e_fallback_sem_desconto():
    antiga = replace(
        fx.track(exercise_id=SUPINO, equipment_id=BANCO, load=30, track_id=9),
        last_executed_at=datetime(2026, 10, 5),
    )
    tracks = {antiga.key: antiga}
    exata, propria = pe.resolve_track(tracks, SUPINO, BANCO, RESTED)
    assert exata is antiga and propria
    fallback, propria = pe.resolve_track(tracks, SUPINO, BANCO, PRE)
    assert not propria
    assert fallback.working_load_kg == 30  # ultima carga de qualquer contexto, sem desconto
    assert fallback.fatigue_context == PRE and fallback.id is None
    assert pe.resolve_track(tracks, SUPINO, BANCO, None) == (antiga, True)
    assert pe.resolve_track(tracks, SUPINO, POLIA, RESTED) == (None, False)


def test_deload_de_outro_contexto_nao_se_aplica():
    descansada = fx.track(exercise_id=SUPINO, equipment_id=BANCO, load=30)
    pre = replace(descansada, fatigue_context=PRE, working_load_kg=20)
    grupo = fx.group(
        muscle_group_id=10, pending_action=pe.DECREASE_LOAD, pending_payload={"track_key": [SUPINO, BANCO, PRE]}
    )
    safety = fx.safety(exercise_id=SUPINO, equipment_id=BANCO)
    assert pe.prescribe(pre, grupo, safety).decision == pe.DECREASE_LOAD
    assert pe.prescribe(descansada, grupo, safety).decision == pe.HOLD
    # alvo legado (gravado antes do contexto): vale para o par
    legado = replace(grupo, pending_payload={"track_key": [SUPINO, BANCO]})
    assert pe.prescribe(descansada, legado, safety).decision == pe.DECREASE_LOAD


def test_prescricao_planejada_usa_a_trilha_do_contexto_da_posicao(monkeypatch):
    from app.intelligence import session_fatigue_reader

    monkeypatch.setattr(session_fatigue_reader, "load_contributions", lambda conn, ids: CONTRIB)
    requests = [
        dp.PairRequest(exercise_id=CRUCIFIXO, equipment_id=BANCO, muscle_group_id=PEITO, model_sets_count=3, sequence=1),
        dp.PairRequest(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO, model_sets_count=3, sequence=2),
    ]
    planned = dp.planned_fatigue_contexts(object(), requests)
    assert [item.fatigue_context for item in planned] == [RESTED, PRE]

    descansada = replace(fx.track(exercise_id=SUPINO, equipment_id=BANCO, load=30), last_executed_at=datetime(2026, 10, 5))
    pre = replace(descansada, fatigue_context=PRE, working_load_kg=20, last_executed_at=datetime(2026, 10, 1))
    tracks = {descansada.key: descansada, pre.key: pre}
    kwargs = dict(muscle_group_id=PEITO, stimulus_weight=1.0, is_compound=True, load_step=None)
    assert dp._track_for(planned[1], tracks, **kwargs).working_load_kg == 20
    assert dp._track_for(replace(planned[1], fatigue_context=RESTED), tracks, **kwargs).working_load_kg == 30


def test_falha_ao_ler_contribuicoes_nao_derruba_a_geracao(monkeypatch):
    from app.intelligence import session_fatigue_reader

    def boom(conn, ids):
        raise RuntimeError("sem banco")

    monkeypatch.setattr(session_fatigue_reader, "load_contributions", boom)
    requests = [dp.PairRequest(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO, sequence=1)]
    assert dp.planned_fatigue_contexts(object(), requests) == requests


# ---------------------------------------------------------------------------
# Migracao 068
# ---------------------------------------------------------------------------


class _FakeMigrationDB:
    def __init__(self, *, exists=True, columns=(), indexes=None, dry_run=False):
        self.exists, self.columns, self.indexes, self.dry_run = exists, set(columns), dict(indexes or {}), dry_run

    def query(self, sql, params=None):
        if "information_schema.TABLES" in sql:
            return [{"TABLE_NAME": "progression_tracks"}] if self.exists else []
        if "information_schema.COLUMNS" in sql:
            tipos = {"fatigue_context": ("varchar(20)", "NO"), "calibrated_at": ("datetime", "YES")}
            return [
                {"COLUMN_NAME": name, "COLUMN_TYPE": tipos[name][0], "IS_NULLABLE": tipos[name][1]}
                for name in self.columns
            ]
        if "information_schema.STATISTICS" in sql:
            return [{"INDEX_NAME": name, "COLUMN_NAME": col} for name, cols in self.indexes.items() for col in cols]
        raise AssertionError(sql)


def _migration():
    from app import migrate as m

    return m.load_python_migration(m.DEFAULT_MIGRATIONS_DIR / "068_progression_track_fatigue_context.py").__globals__


def test_migracao_068_plano_e_reexecucao():
    mod = _migration()
    antigo = {"uk_ptr_user_pair": ["user_id", "exercise_id", "equipment_id"]}
    plano = [sql for sql, _ in mod["plan"](_FakeMigrationDB(indexes=antigo))]
    assert plano == [mod["ADD_CONTEXT_SQL"], mod["ADD_CALIBRATED_SQL"], mod["ADD_NEW_UK_SQL"], mod["DROP_OLD_UK_SQL"]]
    convergido = _FakeMigrationDB(
        columns=("fatigue_context", "calibrated_at"),
        indexes={"uk_ptr_user_pair_context": ["user_id", "exercise_id", "equipment_id", "fatigue_context"]},
    )
    assert mod["plan"](convergido) == []
    assert [sql for sql, _ in mod["plan_down"](convergido)] == [
        mod["ADD_OLD_UK_SQL"], mod["DROP_NEW_UK_SQL"], mod["DROP_CALIBRATED_SQL"], mod["DROP_CONTEXT_SQL"]
    ]
