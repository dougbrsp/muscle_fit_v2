"""Fixture de PARIDADE do replay da progressao (M97 F1).

Um historico deterministico rico (3 exercicios, 2 grupos, 30 sessoes com
progresso, plato, regressao, dor e RPE variado) reprocessado pelo MESMO
caminho do replay de producao (`progression_state.rebuild_for_user`), com a
persistencia dublada em memoria. Devolve o `fingerprint` do `state_snapshot`
(o mesmo md5 que o workflow `replay-progression` mostra ao operador) e um md5
das decisoes gravadas (activity_id + `decision.to_dict()`, que inclui a
evidencia e os rotulos de trilha).

Compativel com o codigo ANTERIOR a M97 F1 de proposito (nada aqui usa API
nova): os valores esperados em `tests/unit/test_m97_f1_paridade_replay.py`
foram calculados rodando ESTE arquivo sobre `origin/develop` antes da F1
(commit fdf23ae) e conferidos depois - historico so de hipertrofia tem o
mesmo estado e as mesmas decisoes.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from typing import Any

from app import progression_state as ps
from app import replay_progression as rp
from tests.support import progression_fixtures as fx

USER = 7
PEITO, COSTAS = 10, 20
SUPINO, CRUCIFIXO, REMADA = 1, 2, 3
BANCO, POLIA = 100, 200

TRACKS = [
    fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO, is_compound=True),
    fx.track(exercise_id=CRUCIFIXO, equipment_id=POLIA, muscle_group_id=PEITO, sequence=2, stimulus_weight=0.6),
    fx.track(exercise_id=REMADA, equipment_id=BANCO, muscle_group_id=COSTAS, sequence=3, is_compound=True),
]


def _reps_for(exercise: int, index: int) -> list[int]:
    # supino: sobe 8 -> 12 e estaciona (plato); crucifixo: oscila; remada: cai no meio (regressao)
    if exercise == SUPINO:
        top = min(12, 8 + index // 2)
        return [top, top, max(8, top - 1)]
    if exercise == CRUCIFIXO:
        return [10 + (index % 3), 10 + ((index + 1) % 3), 10]
    if 12 <= index <= 15 or index >= 26:
        return [7, 6, 6]
    return [11, 10, 10 + (index % 2)]


def _rpe_for(exercise: int, index: int) -> list[Any]:
    if exercise == CRUCIFIXO and index % 5 == 0:
        return [None, None, None]  # sem RPE: sem e1RM
    base = 8 + (index % 3)
    return [min(10, base), min(10, base + 1), 10]


def _weight_for(exercise: int, index: int) -> float:
    if exercise == SUPINO:
        return 40 + 2.5 * (index // 10)
    if exercise == CRUCIFIXO:
        return 15
    return 50 if index < 20 else 52.5


def history() -> list:
    sessions = []
    for index in range(30):
        day = f"2026-07-{1 + index:02d}" if index < 31 else None
        executions = [
            fx.execution(
                exercise_id=SUPINO,
                equipment_id=BANCO,
                sequence=1,
                set_list=fx.sets(
                    reps=_reps_for(SUPINO, index), rpe=_rpe_for(SUPINO, index), weight=_weight_for(SUPINO, index)
                ),
                muscle_pain=3 if index == 17 else None,
            ),
            fx.execution(
                exercise_id=CRUCIFIXO,
                equipment_id=POLIA,
                sequence=2,
                set_list=fx.sets(
                    reps=_reps_for(CRUCIFIXO, index),
                    rpe=_rpe_for(CRUCIFIXO, index),
                    weight=_weight_for(CRUCIFIXO, index),
                ),
            ),
        ]
        if index % 2 == 0:
            executions.append(
                fx.execution(
                    exercise_id=REMADA,
                    equipment_id=BANCO,
                    sequence=3,
                    set_list=fx.sets(
                        reps=_reps_for(REMADA, index), rpe=_rpe_for(REMADA, index), weight=_weight_for(REMADA, index)
                    ),
                )
            )
        sessions.append(fx.session(activity_id=5000 + index * 7, day=day, executions=executions))
    return sessions


class FakeStore:
    """`progression_state` em memoria (escrita e leitura)."""

    def __init__(self) -> None:
        self.groups: dict[Any, Any] = {}
        self.tracks: dict[Any, Any] = {}
        self.decisions: list[dict[str, Any]] = []

    def save_group_state(self, conn, user_id, state):
        self.groups[getattr(state, "key", state.muscle_group_id)] = state
        return 1000 + state.muscle_group_id

    def save_track(self, conn, user_id, track):
        self.tracks[track.key] = track
        return 2000 + len(self.tracks)

    def insert_decision(self, conn, user_id, decision, *, kind="EVALUATION", activity_id=None, **extra):
        self.decisions.append({"kind": kind, "activity_id": activity_id, "decision": decision, **extra})
        return len(self.decisions)

    def load_group_states(self, conn, user_id, training_goal=None):
        return {state.muscle_group_id: state for state in self.groups.values()}

    def load_all_group_states(self, conn, user_id):
        return {state.key: state for state in self.groups.values()}

    def load_tracks(self, conn, user_id):
        return dict(self.tracks)

    def reset(self) -> None:
        self.groups.clear()
        self.tracks.clear()
        self.decisions.clear()


class FakeConn:
    def __init__(self, store: FakeStore) -> None:
        self.store = store

    @contextmanager
    def cursor(self):
        store = self.store

        class _Cursor:
            def execute(self, sql, params=None):
                if sql.startswith("DELETE"):
                    store.reset()

        yield _Cursor()


PATCHED = (
    "save_group_state",
    "save_track",
    "insert_decision",
    "load_group_states",
    "load_all_group_states",
    "load_tracks",
)


def run(monkeypatch=None, sessions=None) -> dict[str, Any]:
    """Replay do historico pela persistencia dublada. `monkeypatch` (pytest)
    ou, sem ele, troca/restaura os atributos a mao (script fora do pytest)."""
    store = FakeStore()
    replaced: dict[str, Any] = {}

    def setattr_(name, value):
        if monkeypatch is not None:
            monkeypatch.setattr(ps, name, value, raising=False)
        else:
            replaced[name] = getattr(ps, name, None)
            setattr(ps, name, value)

    for name in PATCHED:
        setattr_(name, getattr(store, name))
    setattr_("build_session_inputs", lambda conn, user_id, sessions, **_: fx.inputs(tracks=TRACKS))
    try:
        conn = FakeConn(store)
        counts = ps.rebuild_for_user(conn, USER, sessions=sessions if sessions is not None else history())
        fingerprint = rp.fingerprint_of(ps.state_snapshot(conn, USER))
        decisions = json.dumps(
            [[item["activity_id"], item["decision"].to_dict()] for item in store.decisions],
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
    finally:
        for name, value in replaced.items():
            setattr(ps, name, value)
    return {
        "fingerprint": fingerprint,
        "decisions_md5": hashlib.md5(decisions.encode("utf-8")).hexdigest(),
        "decisions": len(store.decisions),
        "counts": {key: counts[key] for key in ("groups", "tracks", "decisions")},
        "store": store,
    }
