"""#241: serie que chegou pendente (`reps_pending`) e e confirmada depois ganha o
`rpe_target_planned` do degrau da rampa da sua posicao (mesma composicao do POST,
dono `effective_intensity`) e as seguintes sao corrigidas como na chegada fora de ordem."""

from __future__ import annotations

from decimal import Decimal

from app import workout_sessions as ws
from tests.unit.test_client_key_sessao_idempotente import SESSION, _identity
from tests.unit.test_rpe_esperado_fora_de_ordem import _make, _planned, _post  # noqa: F401

db = __import__("pytest").fixture(lambda monkeypatch: _make(monkeypatch))


def _pendente(ex, n):
    return _post(ex, n, reps=None, reps_pending=True, rpe=None)


def _confirma(ex, n):
    return ws.update_session_set(
        SESSION, ex, n, ws.SessionSetPatchIn(reps=10, rpe=Decimal(8)), identity=_identity()
    )


def test_pendente_confirmada_ganha_o_degrau_da_posicao(db):
    ex = db.add_exercise()
    _post(ex, 3)  # sozinha: posicao 0 -> 7
    _pendente(ex, 2)  # pendente: sem degrau no envio (alvo efetivo 9)
    _post(ex, 1)  # corrige a 3 (posicao 2 -> 9); a pendente fica
    assert _planned(db) == [7, 9, 9]
    _confirma(ex, 2)
    assert _planned(db) == [7, 8, 9]


def test_pendente_confirmada_e_depois_alcancada(db):
    ex = db.add_exercise()
    _pendente(ex, 2)
    _post(ex, 3)
    _confirma(ex, 2)  # sem a 1: ainda posicao 0 -> 7
    assert _planned(db) == [7, 8]
    _post(ex, 1)  # alcanca as duas: 2 -> 8, 3 -> 9
    assert _planned(db) == [7, 8, 9]


def test_confirmacao_corrige_as_seguintes(db):
    ex = db.add_exercise()
    _pendente(ex, 1)
    _post(ex, 2)
    _post(ex, 3)
    db.sets[0]["rpe_target_planned"] = Decimal(9)  # defasada
    db.sets[1]["rpe_target_planned"] = Decimal(9)
    _confirma(ex, 1)
    assert _planned(db) == [7, 8, 9]


def test_patch_trava_exercicio_antes_das_series(db):
    ex = db.add_exercise()
    _pendente(ex, 1)
    db.sql.clear()
    _confirma(ex, 1)
    flat = [" ".join(s.split()) for s in db.sql]
    i_ex = next(i for i, s in enumerate(flat) if "FROM exercises WHERE id = %s FOR UPDATE" in s)
    i_set = next(i for i, s in enumerate(flat) if "FROM exercise_sets" in s and "FOR UPDATE" in s)
    assert i_ex < i_set


def test_serie_de_tecnica_confirmada_nao_e_replanejada(db):
    ex = db.add_exercise()
    _pendente(ex, 1)
    db.sets[0]["technique_code"] = "DROP_SET"
    antes = db.resync_selects
    _confirma(ex, 1)
    assert db.resync_selects == antes


def test_confirmacao_nao_toca_esperado_nulo_nem_nao_pendente(db):
    ex = db.add_exercise()
    _pendente(ex, 1)
    db.sets[0]["rpe_target_planned"] = None  # importada/sem ficha
    _confirma(ex, 1)
    assert db.sets[0]["rpe_target_planned"] is None
    _post(ex, 2)
    antes = db.resync_updates
    ws.update_session_set(SESSION, ex, 2, ws.SessionSetPatchIn(reps=12), identity=_identity())
    assert db.resync_updates == antes
