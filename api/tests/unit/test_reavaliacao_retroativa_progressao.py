"""`progression_state.reevaluate_after_correction` (#205, lote A): marcar/desmarcar
Aprimoramento de tecnica no historico regrava so a diferenca e chega ao mesmo
estado de `rebuild_for_user`.

Rodar: cd api && python -m pytest tests/unit/test_reavaliacao_retroativa_progressao.py -q --tb=line
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import replace
from itertools import count
from typing import Any

import pytest

from app import progression_state as ps
from tests.support import progression_fixtures as fx
from tests.support import progression_parity_fixture as parity

USER = parity.USER


class Store:
    """Tabelas em memoria com ids e FK em cascata; cada escrita e contada."""

    def __init__(self) -> None:
        self.groups: dict[Any, Any] = {}
        self.tracks: dict[Any, Any] = {}
        self.decisions: list[dict[str, Any]] = []
        self.ids = count(1)
        self.writes = {"group": 0, "track": 0, "decision": 0}

    def save_group_state(self, conn, user_id, state):
        old = self.groups.get(state.key)
        gid = old.id if old is not None else next(self.ids)
        self.groups[state.key] = replace(state, id=gid)
        self.writes["group"] += 1
        return gid

    def save_track(self, conn, user_id, track):
        old = self.tracks.get(track.key)
        tid = old.id if old is not None else next(self.ids)
        self.tracks[track.key] = replace(track, id=tid)
        self.writes["track"] += 1
        return tid

    def insert_decision(self, conn, user_id, decision, *, kind="EVALUATION", activity_id=None, **extra):
        row = {"id": next(self.ids), "kind": kind, "activity_id": activity_id, "group": decision.group_state_id,
               "decision": decision}
        self.decisions.append(row)
        self.writes["decision"] += 1
        return row["id"]

    def execute(self, sql: str, params: tuple) -> None:
        if ps.DECISIONS_TABLE in sql:
            if "activity_id IN" in sql:
                ids = set(params[1:])
                self.decisions = [d for d in self.decisions if d["activity_id"] not in ids]
            else:
                self.decisions.clear()
            return
        table = self.tracks if ps.TRACKS_TABLE in sql else self.groups
        ids = set(params[1:]) if "id IN" in sql else {s.id for s in table.values()}
        for key in [k for k, s in table.items() if s.id in ids]:
            del table[key]
        if table is self.groups:  # ON DELETE CASCADE
            self.decisions = [d for d in self.decisions if d["group"] not in ids]


class Conn:
    def __init__(self, store: Store) -> None:
        self.store = store

    @contextmanager
    def cursor(self):
        store = self.store

        class _Cursor:
            rowcount = 0

            def execute(self, sql, params=None):
                store.execute(sql, tuple(params or ()))

        yield _Cursor()


@pytest.fixture()
def use(monkeypatch):
    monkeypatch.setattr(ps, "build_session_inputs", lambda *a, **k: fx.inputs(tracks=parity.TRACKS))
    monkeypatch.setattr(ps, "shadow_enabled", lambda: True)

    def bind(store: Store) -> Conn:
        for name in ("save_group_state", "save_track", "insert_decision"):
            monkeypatch.setattr(ps, name, getattr(store, name))
        monkeypatch.setattr(ps, "load_all_group_states", lambda conn, uid: dict(store.groups))
        monkeypatch.setattr(ps, "load_tracks", lambda conn, uid: dict(store.tracks))
        return Conn(store)

    return bind


def _supino(session):
    return next(e for e in session.exercises if e.exercise_id == parity.SUPINO)


def _history(*, marcar_em: int | None = None):
    base = parity.history()[:12]
    if marcar_em is not None:
        s = base[marcar_em]
        baixo = replace(_supino(s), form_practice=True, sets=tuple(fx.sets(reps=[4, 4, 3], rpe=10, weight=25)))
        base[marcar_em] = replace(
            s, exercises=tuple(baixo if e.exercise_id == parity.SUPINO else e for e in s.exercises)
        )
    return base


def _view(store, conn):
    decisions = [
        (d["activity_id"], json.dumps(d["decision"].to_dict(), sort_keys=True, default=str))
        for d in sorted(store.decisions, key=lambda d: (d["activity_id"], d["id"]))
    ]
    return ps.state_snapshot(conn, USER), decisions


def _rebuild_view(use, sessions):
    other = Store()
    conn = use(other)
    ps.rebuild_for_user(conn, USER, sessions=sessions)
    return _view(other, conn)


def test_marcar_e_desmarcar_igual_ao_rebuild(use):
    store = Store()
    conn = use(store)
    ps.rebuild_for_user(conn, USER, sessions=_history())
    for marcar in (4, None):
        sessions = _history(marcar_em=marcar)
        conn = use(store)
        summary = ps.reevaluate_after_correction(conn, USER, sessions[4].activity_id, sessions=sessions)
        assert summary["rewritten_sessions"] == len(sessions) - 4
        got = _view(store, conn)
        assert got == _rebuild_view(use, sessions)
    # a marca muda mesmo o resultado (o teste nao e vazio)
    assert _rebuild_view(use, _history(marcar_em=4)) != _rebuild_view(use, _history())


def test_idempotente_nao_grava_trilha_nem_grupo(use):
    store = Store()
    conn = use(store)
    sessions = _history(marcar_em=4)
    ps.rebuild_for_user(conn, USER, sessions=_history())
    ps.reevaluate_after_correction(conn, USER, sessions[4].activity_id, sessions=sessions)
    first = _view(store, conn)
    store.writes.update(group=0, track=0, decision=0)
    ps.reevaluate_after_correction(conn, USER, sessions[4].activity_id, sessions=sessions)
    assert (store.writes["group"], store.writes["track"]) == (0, 0)
    assert _view(store, conn) == first


def test_correcao_na_ultima_sessao_so_regrava_as_decisoes_dela(use):
    store = Store()
    conn = use(store)
    sessions = _history()
    ps.rebuild_for_user(conn, USER, sessions=sessions)
    last = sessions[-1].activity_id
    antigas = {d["id"] for d in store.decisions if d["activity_id"] != last}
    store.writes["decision"] = 0
    summary = ps.reevaluate_after_correction(conn, USER, last, sessions=sessions)
    assert summary["rewritten_sessions"] == 1
    assert antigas <= {d["id"] for d in store.decisions}
    assert 0 < store.writes["decision"] <= 3


def test_sombra_desligada_nao_toca_em_nada(use, monkeypatch):
    store = Store()
    conn = use(store)
    monkeypatch.setattr(ps, "shadow_enabled", lambda: False)
    monkeypatch.setattr(ps, "build_session_inputs", lambda *a, **k: pytest.fail("leu"))
    assert ps.reevaluate_after_correction(conn, USER, 1, sessions=_history()) is None
    assert not store.groups and not store.tracks and not store.decisions
