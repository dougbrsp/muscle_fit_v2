"""Motor de carga por academia (#188): a MESMA regra de calibracao do veredito.

Regra: `.claude/CLAUDE.md` "Motores: fonte unica da verdade"; dono da calibracao
`domain/equipment_context.context_in_calibration`.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest

import app.domain.progression_engine as pe
import app.domain.progression_evaluator as ev
from app.domain.equipment_context import CALIBRATION_MIN_COMPARABLE_SESSIONS, context_in_calibration
from app.domain.training_goal import DEFAULT_GOAL, TrainingGoal
from app.intelligence.dashboard.comparability import (
    VERDICT_LOOKBACK_EXPOSURES,
    build_key,
    comparable_run,
    in_equipment_calibration,
)
from tests.support import progression_fixtures as fx

A, B = 10, 20


def _exec(gym, weight="50", equipment_id=1, machine=True):
    item = fx.execution(equipment_id=equipment_id, set_list=fx.sets(reps=[10, 10, 10], weight=weight, rpe=[8, 8, 8]))
    return replace(item, gym_id=gym, equipment_type_code="MACHINE" if machine else "BARBELL")


def _history(plan):
    return [
        fx.session(activity_id=i, day=f"2026-09-{i:02d}", executions=[_exec(*step)])
        for i, step in enumerate(plan, start=1)
    ]


def _inputs():
    return fx.inputs(tracks=[pe.TrackState(exercise_id=1, equipment_id=1, muscle_group_id=7)])


def _run(plan):
    return ev.evaluate_history(_history(plan), inputs=_inputs())


def _last_outcome(plan):
    sessions = _history(plan)
    partial = ev.evaluate_history(sessions[:-1], inputs=_inputs())
    last = ev.evaluate_session(
        sessions[-1], sessions, group_states=partial.group_states, tracks=partial.tracks, inputs=_inputs()
    )
    return next(iter(last.track_outcomes.values())).reason


def _track(result, gym):
    return next(t for t in result.tracks.values() if t.gym_context == gym)


# --- regra unica (dono) e paridade com a comparabilidade ----------------------

SEQUENCES = {
    "primeiro_contexto_sem_troca": ([(1, A)], False),
    "academia_nova": ([(1, A)] * 3 + [(1, B)], True),
    "academia_nova_2a": ([(1, A)] * 3 + [(1, B)] * 2, True),
    "academia_nova_minimo": ([(1, A)] * 3 + [(1, B)] * CALIBRATION_MIN_COMPARABLE_SESSIONS, False),
    "volta_para_a_com_segmento_cheio": ([(1, A)] * 3 + [(1, B)] * 2 + [(1, A)], False),
    "volta_para_a_com_segmento_curto": ([(1, A), (1, B), (1, A)], True),
    "troca_de_aparelho": ([(1, A)] * 3 + [(2, A)], True),
    "sem_aparelho_no_meio": ([(1, A)] * 3 + [(None, A), (1, A)], False),
    "peso_livre": ([(1, None)] * 3 + [(1, None)], False),
}


def _contexts(seq):
    return [((1, eq) if eq is not None else None, eq, gym) for eq, gym in seq]


def _exposures(seq):
    return [SimpleNamespace(key=build_key(1, eq, "TOTAL", gym_id=gym)) for eq, gym in seq]


@pytest.mark.parametrize("name", list(SEQUENCES))
def test_paridade_dono_comparabilidade(name):
    seq, expected = SEQUENCES[name]
    assert context_in_calibration(_contexts(seq)) is expected
    assert in_equipment_calibration(_exposures(seq)) is expected


# --- motor de carga ----------------------------------------------------------


def test_a_partida_na_academia_nova_vem_da_outra_academia():
    tracks = _run([(A, "60")] * 3).tracks
    track, own = pe.resolve_track(tracks, 1, 1, pe.DEFAULT_FATIGUE_CONTEXT, None, B)
    assert own is False and track.start_from_other_gym and track.in_context_calibration
    assert track.working_load_kg == Decimal("60") and track.id is None and track.pending_load_kg is None
    decision = pe.prescribe(track, fx.group(), fx.safety())
    assert decision.calibrating and decision.calibration_code == pe.CALIBRATION_CODE_START_OTHER_GYM
    assert decision.suggested_load_kg == 60.0


def test_b_calibracao_segura_a_carga_mesmo_com_pendencia_de_grupo():
    result = _run([(A, "60")] * 3 + [(B, "40"), (B, "45")])
    track = _track(result, B)
    assert track.in_context_calibration and track.working_load_kg == Decimal("45")
    assert _last_outcome([(A, "60")] * 3 + [(B, "40"), (B, "45")]) == pe.REASON_TRACK_IN_CALIBRATION
    for action in (pe.INCREASE_LOAD, pe.DECREASE_LOAD):
        decision = pe.prescribe(track, replace(fx.group(), pending_action=action), fx.safety())
        assert decision.suggested_load_kg == 45.0 and decision.decision == pe.HOLD
        assert decision.calibrating and pe.BLOCKED_CONTEXT_CALIBRATION in decision.blocked_by


def test_c_depois_do_minimo_a_trilha_decide():
    result = _run([(A, "60")] * 3 + [(B, "40")] * CALIBRATION_MIN_COMPARABLE_SESSIONS)
    track = _track(result, B)
    assert track.load_status == pe.ACTIVE
    plan = [(A, "60")] * 3 + [(B, "40")] * CALIBRATION_MIN_COMPARABLE_SESSIONS
    assert _last_outcome(plan) != pe.REASON_TRACK_IN_CALIBRATION
    assert not pe.prescribe(track, fx.group(), fx.safety()).calibrating


def test_d_volta_para_a_segue_o_dono():
    result = _run([(A, "60")] * 3 + [(B, "40")] * 2 + [(A, "60")])
    assert _track(result, A).load_status == pe.ACTIVE


def test_g_sem_troca_primeira_trilha_nao_calibra():
    assert _track(_run([(A, "60")]), A).load_status == pe.ACTIVE


def _plan_sessions(plan, start=1):
    """plan = [(equipment_id, gym, goal)] -> sessoes de uma execucao cada."""
    return [
        fx.session(
            activity_id=i,
            day=f"2026-09-{i:02d}",
            executions=[replace(_exec(gym, "60", equipment_id=eq), training_goal=goal)],
        )
        for i, (eq, gym, goal) in enumerate(plan, start=start)
    ]


def _parity(sessions):
    """Para cada sessao: (motor de carga, comparabilidade) sobre o mesmo historico."""
    pares = []
    for index, session in enumerate(sessions):
        execution = session.exercises[0]
        engine = ev.context_calibrating(execution, sessions[: index + 1], session, _inputs())
        exposures = [
            SimpleNamespace(key=build_key(1, s.exercises[0].equipment_id, "TOTAL", gym_id=s.exercises[0].gym_id))
            for s in sessions[: index + 1]
        ]
        pares.append((engine, comparable_run(exposures).in_calibration))
    return pares


HYP = DEFAULT_GOAL.value
OTHER = next(goal.value for goal in TrainingGoal if goal != DEFAULT_GOAL)


def test_paridade_janela_do_veredito_em_sequencia_longa():
    # 1 em A, 7 em B, 2 em A: lida inteira, A teria 3 sessoes (minimo); nas
    # ultimas VERDICT_LOOKBACK_EXPOSURES so 2 -> calibra nos dois motores.
    plan = [(1, A, HYP)] + [(1, B, HYP)] * 7 + [(1, A, HYP)] * 2
    assert len(plan) > VERDICT_LOOKBACK_EXPOSURES
    pares = _parity(_plan_sessions(plan))
    assert all(engine is verdict for engine, verdict in pares), pares
    assert pares[-1] == (True, True)


def test_paridade_objetivo_misto_conta_o_outro_objetivo():
    # X de hipertrofia, Y (outro aparelho) de outro objetivo, volta para X:
    # a comparabilidade ve a troca; o motor tambem (janela de todos os objetivos).
    plan = [(1, A, HYP)] * 3 + [(2, A, OTHER), (1, A, HYP)]
    sessions = _plan_sessions(plan)
    view = [s.progression_view(HYP) for s in sessions]
    last = view[-1].exercises[0]
    assert ev.context_calibrating(last, view, view[-1], _inputs(), sessions) is True
    assert comparable_run(
        [SimpleNamespace(key=build_key(1, s.exercises[0].equipment_id, "TOTAL", gym_id=A)) for s in sessions]
    ).in_calibration
    # ponta a ponta pelo replay por objetivo: a trilha de X calibra de novo
    result = ev.evaluate_history_by_goal(sessions, inputs=_inputs()).results[HYP]
    track_x = next(t for t in result.tracks.values() if t.equipment_id == 1)
    assert track_x.in_context_calibration and track_x.working_load_kg == Decimal("60")


def test_e_troca_de_aparelho_e_volta_recalibra_x_sem_mexer_na_carga():
    def track_x(result):
        return next(t for t in result.tracks.values() if t.equipment_id == 1 and t.gym_context == A)

    before = _run([(A, "60")] * 3)
    plan = [(A, "60")] * 3 + [(A, "60", 2), (A, "60")]
    result = _run(plan)
    assert _last_outcome(plan) == pe.REASON_TRACK_IN_CALIBRATION
    x_before, x_after = track_x(before), track_x(result)
    assert x_after.working_load_kg == x_before.working_load_kg == Decimal("60")
    # pendencia armada antes da troca: a calibracao nao a apaga nem a aplica
    assert x_after.pending_load_kg == x_before.pending_load_kg
    decision = pe.prescribe(x_after, replace(fx.group(), pending_action=pe.INCREASE_LOAD), fx.safety())
    assert decision.decision == pe.HOLD and decision.suggested_load_kg == 60.0 and decision.calibrating


def test_f_peso_livre_igual_ao_de_hoje():
    free = [replace(_exec(g, "60", machine=False)) for g in (A, B, A, B)]
    sessions = [fx.session(activity_id=i, day=f"2026-09-{i:02d}", executions=[e]) for i, e in enumerate(free, 1)]
    baseline = [replace(s, exercises=tuple(replace(e, gym_id=None) for e in s.exercises)) for s in sessions]
    with_gym = ev.evaluate_history(sessions, inputs=_inputs())
    without = ev.evaluate_history(baseline, inputs=_inputs())
    assert with_gym.tracks == without.tracks
    assert [d.decision for d in with_gym.decisions] == [d.decision for d in without.decisions]


# --- rodada 1 (#188): helpers do dono e persistencia por academia -----------------------------------------
from datetime import datetime  # noqa: E402

import app.progression_state as ps  # noqa: E402
from app.domain.equipment_context import CALIBRATION_CODE_START_OTHER_GYM, latest_in_gym_context  # noqa: E402


def _t(gym, day, track_id, load="50", goal=DEFAULT_GOAL.value, ctx="rested", eq=1):
    return pe.TrackState(
        exercise_id=1, equipment_id=eq, id=track_id, gym_context=gym, training_goal=goal, fatigue_context=ctx,
        working_load_kg=None if load is None else Decimal(load), last_executed_at=datetime(2026, 11, day),
    )


def test_latest_in_gym_context_regra_unica():
    ctx = lambda item: item[0]  # noqa: E731
    assert latest_in_gym_context([], 7, ctx) == (None, False)
    assert latest_in_gym_context([(7, "a"), (8, "b")], None, ctx) == ((8, "b"), False)
    # ha item neste contexto: o mais recente DELE, mesmo mais antigo que o de outra academia
    assert latest_in_gym_context([(7, "a"), (8, "b")], 7, ctx) == ((7, "a"), False)
    # nada neste contexto: o mais recente de outra academia de maquina, como partida
    assert latest_in_gym_context([(8, "b"), (9, "c"), (None, "livre")], 7, ctx) == ((9, "c"), True)
    assert latest_in_gym_context([(None, "livre")], 7, ctx) == (None, False)
    assert pe.CALIBRATION_CODE_START_OTHER_GYM == CALIBRATION_CODE_START_OTHER_GYM


def test_current_track_for_pair_mais_recente_qualquer_objetivo_e_contexto():
    tracks = [
        _t(A, 1, 1),
        _t(A, 3, 2, goal=TrainingGoal.STRENGTH.value, ctx="fatigued"),
        _t(B, 5, 3),
        _t(None, 4, 4, eq=2),
    ]
    assert pe.current_track_for_pair(tracks, 1, 1).id == 3
    assert pe.current_track_for_pair(tracks, 1, 1, gym_context=A).id == 2
    assert pe.current_track_for_pair({t.key: t for t in tracks}, 1, 1, A).id == 2
    assert pe.current_track_for_pair(tracks, 1, 1, gym_context=99) is None
    assert pe.current_track_for_pair(tracks, 1, 2).id == 4
    assert pe.current_track_for_pair(tracks, 1, None) is None
    # empate na data: maior id
    assert pe.current_track_for_pair([_t(A, 1, 5), _t(A, 1, 6)], 1, 1).id == 6


def test_resolve_track_partida_de_outra_academia_e_preferencia_pela_propria():
    own_old, other_new = _t(A, 1, 1), _t(B, 9, 2)
    tracks = {t.key: t for t in (own_old, other_new)}
    assert pe.resolve_track(tracks, 1, 1, "rested", None, A) == (own_old, True)
    start, own = pe.resolve_track(tracks, 1, 1, "rested", None, 77)
    assert own is False and start.start_from_other_gym and start.gym_context == 77
    assert start.working_load_kg == other_new.working_load_kg and start.id is None
    # trilha de outra academia sem carga nao serve de partida
    empty = _t(B, 9, 3, load=None)
    assert pe.resolve_track({empty.key: empty}, 1, 1, "rested", None, 77) == (None, False)


class _FakeTracksDb:
    """Tabela `progression_tracks` em memoria: casa `col = %s` com os parametros, na ordem."""

    def __init__(self):
        self.rows: list[dict] = []
        self.rowcount = 0
        self._result: list[dict] = []

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @staticmethod
    def _cols(sql):
        import re

        return [c.split(".")[-1] for c in re.findall(r"([\w.]+)\s*=\s*%s", sql)]

    def execute(self, sql, params):
        params = tuple(params)
        if sql.startswith("INSERT"):
            cols = [c.strip() for c in sql.split("(", 1)[1].split(")", 1)[0].split(",")]
            assert len(cols) == len(params)
            self.rows.append({"id": len(self.rows) + 1, **dict(zip(cols, params))})
        elif sql.startswith("UPDATE"):
            cols = self._cols(sql)
            assert len(cols) == len(params)
            n_where = len(self._cols(sql.split("WHERE", 1)[1]))
            sets, where = dict(zip(cols[:-n_where], params[:-n_where])), dict(zip(cols[-n_where:], params[-n_where:]))
            hit = [r for r in self.rows if all(r.get(k) == v for k, v in where.items())]
            for row in hit:
                row.update(sets)
            self.rowcount = len(hit)
        elif "WHERE user_id = %s AND exercise_id" in sql:
            where = dict(zip(self._cols(sql), params))
            self._result = [r for r in self.rows if all(r.get(k) == v for k, v in where.items())]
        else:
            self._result = [r for r in self.rows if r["user_id"] == params[0]]

    def fetchone(self):
        return self._result[0]

    def fetchall(self):
        return self._result


def test_persistencia_por_academia_ida_e_volta():
    db = _FakeTracksDb()
    free = pe.TrackState(exercise_id=1, equipment_id=2, working_load_kg=Decimal("40"), gym_context=None)
    machine = replace(free, gym_context=7, working_load_kg=Decimal("55"))
    assert ps.save_track(db, 5, free) == 1
    assert ps.save_track(db, 5, machine) == 2
    assert [r["gym_context_id"] for r in db.rows] == [ps.NO_GYM_CONTEXT_ID, 7]
    read = ps._read_tracks(db, 5)
    assert {k: (t.gym_context, t.working_load_kg) for k, t in read.items()} == {
        free.key: (None, Decimal("40")),
        machine.key: (7, Decimal("55")),
    }
    # UPDATE com id: a ordem dos parametros acerta a linha da academia, nao a de peso livre
    ps.save_track(db, 5, replace(read[machine.key], working_load_kg=Decimal("60")))
    assert [r["working_load_kg"] for r in db.rows] == [Decimal("40"), Decimal("60")]
    assert ps._read_tracks(db, 5)[free.key].gym_context is None


def test_paridade_troca_de_convencao_no_mesmo_aparelho():
    # #188: A, depois B na convencao X, depois B na convencao Y (mesmo aparelho):
    # os dois motores montam a MESMA chave fisica (`calibration_physical_key`) e
    # dao a mesma resposta em cada passo.
    from app.load_conventions import LoadConvention

    x, y = list(LoadConvention)[:2]
    plan = [(A, x)] * 3 + [(B, x)] * 2 + [(B, y)] * 2
    sessions = [
        fx.session(activity_id=i, day=f"2026-09-{i:02d}", executions=[replace(_exec(gym), load_convention=conv.value)])
        for i, (gym, conv) in enumerate(plan, start=1)
    ]
    pares = []
    for index, session in enumerate(sessions):
        engine = ev.context_calibrating(session.exercises[0], sessions[: index + 1], session, _inputs())
        exposures = [
            SimpleNamespace(key=build_key(1, 1, s.exercises[0].load_convention, gym_id=s.exercises[0].gym_id))
            for s in sessions[: index + 1]
        ]
        pares.append((engine, comparable_run(exposures).in_calibration))
    assert all(engine is verdict for engine, verdict in pares), pares
    assert [engine for engine, _ in pares] == [False, False, False, True, True, False, False]
