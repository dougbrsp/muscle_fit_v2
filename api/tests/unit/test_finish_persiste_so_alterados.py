"""`finish`: a persistencia da progressao grava so o NOVO ou ALTERADO.

Bug de producao (03/11): `persist_result` chamava `save_group_state` e
`save_track` para TODOS os estados do resultado (19 grupos, 209 trilhas = ~456
chamadas da RDS Data API), mesmo os que a sessao nao tocou, e o `finish`
estourava o teto de 29 s do API Gateway. Aqui, com cursor falso:

1. o numero de statements e proporcional aos alterados, nao ao total;
2. o estado final e igual ao da gravacao completa (inclui trilha/grupo novo,
   com `id` None, e decisoes com `group_state_id` resolvido);
3. o replay (sem baseline) continua gravando tudo;
4. `run_session_evaluation` entrega o estado carregado como baseline.

Rodar: cd api && python -m pytest tests/unit/test_finish_persiste_so_alterados.py -q --tb=line
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from itertools import count
from typing import Any

from app import progression_state as ps
from app.domain import progression_engine as pe
from app.domain import progression_evaluator as ev

USER = 7
N_GROUPS = 19
N_TRACKS = 209
TOUCHED_GROUPS = (3, 4, 5, 6, 7, 8)  # 6 grupos
TOUCHED_TRACKS = tuple(range(1, 13))  # 12 trilhas
NEW_GROUP_ID = 900
NEW_EXERCISE_ID = 9000


class FakeCursor:
    def __init__(self, conn: "FakeConn") -> None:
        self.conn = conn
        self.rowcount = 0
        self.lastrowid = 0
        self._result: dict[str, Any] | None = None

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def execute(self, sql: str, params: tuple = ()) -> None:
        self.conn.statements.append(sql.split()[0])
        table = ps.GROUP_STATES_TABLE if ps.GROUP_STATES_TABLE in sql else ps.TRACKS_TABLE
        key_len = 3 if table == ps.GROUP_STATES_TABLE else 5
        rows = self.conn.tables[table]
        if sql.startswith("UPDATE"):
            key = tuple(params[-key_len:])
            if key in rows:
                rows[key] = (rows[key][0], tuple(params[:-key_len]))
                self.rowcount = 1
            else:
                self.rowcount = 0
        elif sql.startswith("INSERT INTO " + table):
            key = tuple(params[:key_len])
            rows[key] = (next(self.conn.ids), tuple(params[key_len:]))
            self.lastrowid = rows[key][0]
        elif sql.startswith("INSERT INTO " + ps.DECISIONS_TABLE):
            self.conn.decisions.append((params[0], params[1], params[4]))
            self.lastrowid = len(self.conn.decisions)
        elif sql.startswith("SELECT id"):
            self._result = {"id": rows[tuple(params)][0]}
        else:  # pragma: no cover
            raise AssertionError(sql)

    def fetchone(self) -> dict[str, Any] | None:
        return self._result


class FakeConn:
    def __init__(self) -> None:
        self.tables: dict[str, dict[tuple, tuple]] = {ps.GROUP_STATES_TABLE: {}, ps.TRACKS_TABLE: {}}
        self.ids = count(1000)
        self.statements: list[str] = []
        self.decisions: list[tuple] = []

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def clone(self) -> "FakeConn":
        other = FakeConn()
        other.tables = {name: dict(rows) for name, rows in self.tables.items()}
        other.ids = count(1000)
        return other


def _group(group_id: int, **extra: Any) -> pe.GroupState:
    return pe.GroupState(muscle_group_id=group_id, id=group_id, **extra)


def _track(index: int, **extra: Any) -> pe.TrackState:
    return pe.TrackState(
        exercise_id=index,
        equipment_id=1,
        muscle_group_id=index % N_GROUPS + 1,
        id=index,
        load_status=pe.CALIBRATION,
        working_load_kg=Decimal("40"),
        **extra,
    )


def _loaded() -> tuple[dict, dict]:
    groups = {state.key: state for state in (_group(i) for i in range(1, N_GROUPS + 1))}
    tracks = {track.key: track for track in (_track(i) for i in range(1, N_TRACKS + 1))}
    return groups, tracks


def _seed(conn: FakeConn, groups: dict, tracks: dict) -> None:
    """O banco ja tem exatamente o que foi carregado (valores + engine_version)."""
    for state in groups.values():
        key = (USER, state.muscle_group_id, state.training_goal)
        conn.tables[ps.GROUP_STATES_TABLE][key] = (state.id, ps._group_values(state) + (pe.ENGINE_VERSION,))
    for track in tracks.values():
        key = (USER, track.exercise_id, track.equipment_id, track.fatigue_context, track.training_goal)
        conn.tables[ps.TRACKS_TABLE][key] = (track.id, ps._track_values(track) + (pe.ENGINE_VERSION,))


def _result(groups: dict, tracks: dict) -> ev.EvaluationResult:
    """O que o avaliador devolve: tudo que foi carregado, com os tocados
    alterados e um grupo e uma trilha NOVOS (id None)."""
    new_groups = {state.muscle_group_id: state for state in groups.values()}
    for group_id in TOUCHED_GROUPS:
        new_groups[group_id] = replace(new_groups[group_id], reps_min=8, reps_max=10)
    new_groups[NEW_GROUP_ID] = pe.GroupState(muscle_group_id=NEW_GROUP_ID)
    new_tracks = dict(tracks)
    for index in TOUCHED_TRACKS:
        old = _track(index)
        new_tracks[old.key] = replace(old, working_load_kg=Decimal("42.5"), load_status=pe.LOAD_NOT_APPLICABLE)
    fresh = pe.TrackState(exercise_id=NEW_EXERCISE_ID, equipment_id=1, muscle_group_id=NEW_GROUP_ID)
    new_tracks[fresh.key] = fresh
    decisions = tuple(
        pe.ProgressionDecision(
            group_state_id=None if group_id == NEW_GROUP_ID else group_id,
            muscle_group_id=group_id,
            track_id=None,
            engine_version=pe.ENGINE_VERSION,
            phase_before=pe.F1,
            phase_after=pe.F1,
            decision=pe.HOLD,
            suggested_reps_min=6,
            suggested_reps_max=8,
            can_progress=False,
            blocked_by=(),
            evidence={},
            next_eligible_progression_at=None,
            reason="teste",
        )
        for group_id in (*TOUCHED_GROUPS, NEW_GROUP_ID)
    )
    return ev.EvaluationResult(group_states=new_groups, tracks=new_tracks, decisions=decisions)


def _run(selective: bool) -> tuple[FakeConn, dict]:
    groups, tracks = _loaded()
    conn = FakeConn()
    _seed(conn, groups, tracks)
    result = _result(groups, tracks)
    kwargs = {"baseline_groups": groups, "baseline_tracks": tracks} if selective else {}
    counts = ps.persist_result(conn, USER, result, activity_id=55, **kwargs)
    return conn, counts


def test_statements_proporcionais_aos_alterados_nao_ao_total() -> None:
    full, _ = _run(selective=False)
    selective, _ = _run(selective=True)
    # completo: pelo menos 1 UPDATE por grupo/trilha carregado (antes eram 2, com o SELECT id)
    assert len(full.statements) >= N_GROUPS + N_TRACKS
    # seletivo: 1 UPDATE por tocado; o novo faz UPDATE+INSERT+SELECT; + 7 decisoes
    esperado = (
        len(TOUCHED_GROUPS) + len(TOUCHED_TRACKS)  # UPDATE sem SELECT de volta
        + 3 + 3  # grupo novo e trilha nova: UPDATE + INSERT + SELECT id
        + len(TOUCHED_GROUPS) + 1  # decisoes
    )
    assert len(selective.statements) == esperado
    assert len(selective.statements) < len(full.statements) / 5
    assert selective.statements.count("SELECT") == 2  # so os dois novos (id None) precisam do id de volta


def test_estado_final_igual_ao_da_gravacao_completa() -> None:
    full, full_counts = _run(selective=False)
    selective, selective_counts = _run(selective=True)
    assert selective.tables[ps.GROUP_STATES_TABLE] == full.tables[ps.GROUP_STATES_TABLE]
    assert selective.tables[ps.TRACKS_TABLE] == full.tables[ps.TRACKS_TABLE]
    # decisoes com o group_state_id resolvido (inclusive o do grupo novo, id None)
    assert selective.decisions == full.decisions
    assert all(group_state_id is not None for group_state_id, _, _ in selective.decisions)
    assert selective_counts == full_counts
    assert selective_counts["groups"] == N_GROUPS + 1
    assert selective_counts["tracks"] == N_TRACKS + 1


def test_sem_alteracao_nao_grava_estado_nem_esquece_o_memo(monkeypatch) -> None:
    groups, tracks = _loaded()
    conn = FakeConn()
    _seed(conn, groups, tracks)
    forgotten: list[str] = []
    monkeypatch.setattr(ps.request_memo, "forget", lambda name: forgotten.append(name))
    result = ev.EvaluationResult(group_states={s.muscle_group_id: s for s in groups.values()}, tracks=dict(tracks))
    counts = ps.persist_result(conn, USER, result, baseline_groups=groups, baseline_tracks=tracks)
    assert conn.statements == []
    assert forgotten == []
    assert counts == {"groups": N_GROUPS, "tracks": N_TRACKS, "decisions": 0}
    # com gravacao, o memo das trilhas e esquecido
    changed = replace(next(iter(tracks.values())), working_load_kg=Decimal("50"))
    result = ev.EvaluationResult(group_states={}, tracks={changed.key: changed})
    ps.persist_result(conn, USER, result, baseline_groups=groups, baseline_tracks=tracks)
    assert forgotten == [ps.TRACKS_MEMO]


def test_replay_sem_baseline_grava_tudo() -> None:
    groups, tracks = _loaded()
    conn = FakeConn()  # replay apaga o estado antes: banco vazio
    result = ev.EvaluationResult(
        group_states={s.muscle_group_id: replace(s, id=None) for s in groups.values()},
        tracks={k: replace(t, id=None) for k, t in tracks.items()},
    )
    evaluation = ev.GoalEvaluation(results={"hypertrophy": result})
    counts = ps.persist_goal_evaluation(conn, USER, evaluation, per_session=True)
    assert counts == {"groups": N_GROUPS, "tracks": N_TRACKS, "decisions": 0}
    assert len(conn.tables[ps.GROUP_STATES_TABLE]) == N_GROUPS
    assert len(conn.tables[ps.TRACKS_TABLE]) == N_TRACKS
    assert "baseline" not in ps.rebuild_for_user.__code__.co_names


def test_run_session_evaluation_entrega_o_estado_carregado_como_baseline(monkeypatch) -> None:
    groups, tracks = _loaded()
    seen: dict[str, Any] = {}

    class History:
        def __init__(self, conn: Any) -> None: ...

        def fetch_last_completed_sessions(self, user_id: int, window: int) -> list:
            return [_Session()]

    class _Session:
        activity_id = 55

    monkeypatch.setattr(ps, "TrainingHistoryRepository", History)
    monkeypatch.setattr(ps, "build_session_inputs", lambda *a, **k: object())
    monkeypatch.setattr(ps, "load_all_group_states", lambda conn, user_id: groups)
    monkeypatch.setattr(ps, "load_tracks", lambda conn, user_id: tracks)
    monkeypatch.setattr(
        ps, "evaluate_session_by_goal", lambda *a, **k: ev.GoalEvaluation(results={})
    )

    def fake_persist(conn: Any, user_id: int, result: Any, **kwargs: Any) -> dict[str, int]:
        seen.update(kwargs)
        return {"groups": 0, "tracks": 0, "decisions": 0}

    monkeypatch.setattr(ps, "persist_goal_evaluation", fake_persist)
    ps.run_session_evaluation(object(), USER, 55)
    assert seen["baseline_groups"] is groups
    assert seen["baseline_tracks"] is tracks
