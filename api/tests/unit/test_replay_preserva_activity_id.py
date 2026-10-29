"""O replay (`progression_state.rebuild_for_user`) grava cada decisao com a
sessao que a gerou.

Bug corrigido: `evaluate_history` juntava as decisoes de todas as sessoes numa
lista plana e `rebuild_for_user` chamava `persist_result` sem `activity_id`,
entao todo replay regravava o historico de `progression_decisions` com
`activity_id` NULL. O fluxo do `finish` (`run_session_evaluation`) ja gravava
certo.

Aqui, sem banco: as funcoes de escrita/leitura de `progression_state` sao
dubladas por um "banco" em memoria, para provar (1) o vinculo decisao ->
sessao e (2) que o ESTADO gravado (trilhas e grupos) e identico ao do caminho
antigo, pelo mesmo `fingerprint` que o operador ve no resumo do replay.

Rodar: cd api && python -m pytest tests/unit/test_replay_preserva_activity_id.py
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pytest

from app import progression_state as ps
from app import replay_progression as rp
from app.domain import progression_evaluator as ev
from tests.support import progression_fixtures as fx

USER = 7
PEITO, COSTAS = 10, 20
SUPINO, CRUCIFIXO, REMADA = 1, 2, 3
BANCO = 100

TRACKS = [
    fx.track(exercise_id=SUPINO, equipment_id=BANCO, muscle_group_id=PEITO),
    fx.track(exercise_id=CRUCIFIXO, equipment_id=BANCO, muscle_group_id=PEITO, sequence=2),
    fx.track(exercise_id=REMADA, equipment_id=BANCO, muscle_group_id=COSTAS, sequence=3),
]
DAYS = ["2026-08-01", "2026-08-09", "2026-08-12", "2026-08-20", "2026-08-23", "2026-08-31", "2026-09-03"]


def _exec(exercise_id: int, sequence: int):
    return fx.execution(
        exercise_id=exercise_id,
        equipment_id=BANCO,
        sequence=sequence,
        set_list=fx.sets(reps=12, rpe=10, weight=40, count=3),
    )


def _sessions():
    """Sete sessoes (ids nao sequenciais, como no banco); peito em todas,
    costas so nas de indice par - assim ha sessoes com quantidades diferentes
    de decisoes e o agrupamento nao pode ser deduzido pela posicao."""
    sessions = []
    for index, day in enumerate(DAYS):
        executions = [_exec(SUPINO, 1), _exec(CRUCIFIXO, 2)]
        if index % 2 == 0:
            executions.append(_exec(REMADA, 3))
        sessions.append(fx.session(activity_id=900 + index * 13, day=day, executions=executions))
    return sessions


class FakeStore:
    """`progression_state` em memoria: o que `persist_result` grava e o que
    `state_snapshot` le de volta."""

    def __init__(self) -> None:
        self.groups: dict[int, Any] = {}
        self.tracks: dict[Any, Any] = {}
        self.decisions: list[dict[str, Any]] = []

    def save_group_state(self, conn, user_id, state):
        self.groups[state.muscle_group_id] = state
        return 1000 + state.muscle_group_id

    def save_track(self, conn, user_id, track):
        self.tracks[(track.exercise_id, track.equipment_id)] = track
        return 2000 + len(self.tracks)

    def insert_decision(self, conn, user_id, decision, *, kind="EVALUATION", activity_id=None, **_):
        self.decisions.append({"kind": kind, "activity_id": activity_id, "decision": decision})
        return len(self.decisions)

    def load_group_states(self, conn, user_id, training_goal=None):
        return dict(self.groups)

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


@pytest.fixture
def store(monkeypatch):
    fake = FakeStore()
    for name in (
        "save_group_state",
        "save_track",
        "insert_decision",
        "load_group_states",
        "load_all_group_states",
        "load_tracks",
    ):
        monkeypatch.setattr(ps, name, getattr(fake, name))
    monkeypatch.setattr(ps, "build_session_inputs", lambda conn, user_id, sessions, **_: fx.inputs(tracks=TRACKS))
    return fake


def _fingerprint(store: FakeStore) -> str:
    return rp.fingerprint_of(ps.state_snapshot(FakeConn(store), USER))


# ===========================================================================
# evaluate_history: agrupamento por sessao, compativel com a lista plana
# ===========================================================================


def test_evaluate_history_agrupa_as_decisoes_pela_sessao_de_origem():
    sessions = _sessions()
    result = ev.evaluate_history(sessions, inputs=fx.inputs(tracks=TRACKS))

    assert [batch.activity_id for batch in result.session_decisions] == [s.activity_id for s in sessions]
    # mesmas decisoes, mesma ordem: a lista plana continua valendo
    flattened = [d for batch in result.session_decisions for d in batch.decisions]
    assert [d.to_dict() for d in flattened] == [d.to_dict() for d in result.decisions]
    # uma decisao por grupo tocado: sessoes com costas tem uma a mais
    sizes = [len(batch.decisions) for batch in result.session_decisions]
    assert sizes == [2 if index % 2 == 0 else 1 for index in range(len(sessions))]


def test_cada_grupo_bate_com_a_avaliacao_isolada_da_sessao():
    """O grupo da sessao N e exatamente o que `evaluate_session` devolve para
    ela com o estado acumulado ate N-1."""
    sessions = _sessions()
    inputs = fx.inputs(tracks=TRACKS)
    result = ev.evaluate_history(sessions, inputs=inputs)
    groups: dict = {}
    tracks: dict = {}
    for index, session in enumerate(sessions):
        window = sessions[max(0, index + 1 - 21) : index + 1]
        step = ev.evaluate_session(session, window, group_states=groups, tracks=tracks, inputs=inputs)
        groups, tracks = step.group_states, step.tracks
        batch = result.session_decisions[index]
        assert batch.activity_id == session.activity_id
        assert [d.to_dict() for d in batch.decisions] == [d.to_dict() for d in step.decisions]


# ===========================================================================
# rebuild_for_user: activity_id gravado, estado identico
# ===========================================================================


def test_rebuild_grava_cada_decisao_com_a_sessao_que_a_gerou(store):
    sessions = _sessions()
    summary = ps.rebuild_for_user(FakeConn(store), USER, sessions=sessions)

    assert summary["decisions"] == len(store.decisions) > 0
    evaluations = [row for row in store.decisions if row["kind"] == "EVALUATION"]
    assert evaluations == store.decisions
    assert all(row["activity_id"] is not None for row in evaluations)

    expected = ev.evaluate_history(sessions, inputs=fx.inputs(tracks=TRACKS))
    origem = [batch.activity_id for batch in expected.session_decisions for _ in batch.decisions]
    assert [row["activity_id"] for row in evaluations] == origem
    assert set(origem) == {s.activity_id for s in sessions}


def test_rebuild_nao_muda_o_estado_nem_as_decisoes_so_o_vinculo(store):
    """Prova byte a byte: o caminho ANTIGO (`persist_result` sem
    `activity_id`, a lista plana) e o novo gravam o mesmo estado - mesmo
    `fingerprint` de `replay_progression` - e as mesmas decisoes na mesma
    ordem; so o `activity_id` muda de NULL para a sessao de origem."""
    sessions = _sessions()
    conn = FakeConn(store)

    antigo = ev.evaluate_history(sessions, inputs=fx.inputs(tracks=TRACKS))
    ps.persist_result(conn, USER, antigo)  # exatamente o que o replay fazia
    fingerprint_antigo = _fingerprint(store)
    decisoes_antigas = list(store.decisions)
    assert {row["activity_id"] for row in decisoes_antigas} == {None}  # o bug

    ps.rebuild_for_user(conn, USER, sessions=sessions)
    assert _fingerprint(store) == fingerprint_antigo
    assert len(store.decisions) == len(decisoes_antigas)
    for nova, velha in zip(store.decisions, decisoes_antigas):
        assert nova["decision"] == velha["decision"]
        assert nova["decision"].to_dict() == velha["decision"].to_dict()
        assert nova["activity_id"] is not None


def test_rebuild_duas_vezes_mesmo_fingerprint_e_mesmos_vinculos(store):
    sessions = _sessions()
    conn = FakeConn(store)
    ps.rebuild_for_user(conn, USER, sessions=sessions)
    primeiro = (_fingerprint(store), [row["activity_id"] for row in store.decisions])
    ps.rebuild_for_user(conn, USER, sessions=sessions)
    assert (_fingerprint(store), [row["activity_id"] for row in store.decisions]) == primeiro


def test_finish_continua_gravando_tudo_com_o_activity_id_recebido(store):
    """Sem `per_session`, o comportamento do `finish` nao muda."""
    sessions = _sessions()
    result = ev.evaluate_session(
        sessions[0], sessions[:1], group_states={}, tracks={}, inputs=fx.inputs(tracks=TRACKS)
    )
    ps.persist_result(FakeConn(store), USER, result, activity_id=sessions[0].activity_id)
    assert store.decisions
    assert {row["activity_id"] for row in store.decisions} == {sessions[0].activity_id}
