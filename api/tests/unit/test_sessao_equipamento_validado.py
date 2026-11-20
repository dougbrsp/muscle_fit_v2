"""POST /workout-sessions/{id}/exercises valida o `equipment_id` explicito (#163):
associacao existente, da academia da sessao e coerente com o exercicio; a
inativa e aceita com log; o reenvio por `client_key` nao revalida."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app import workout_sessions as ws
from tests.unit import test_client_key_sessao_idempotente as base
from tests.unit.test_client_key_sessao_idempotente import FakeSessionDB, _add_exercise, _install

GYM = 3  # academia da sessao no FakeSessionDB


class FakeDB(FakeSessionDB):
    def __init__(self):
        super().__init__()
        self.ge = {2: {"gym_id": GYM, "is_active": 1}}
        self.coherent = True

    def execute(self, sql, params=None):
        if " ".join(sql.split()).startswith("SELECT gym_id, is_active FROM gym_equipment"):
            self.sql.append(sql)
            self._result = self.ge.get(params[0])
            return
        super().execute(sql, params)


@pytest.fixture
def db(monkeypatch):
    fake = _install(monkeypatch, FakeDB())
    # `_install` troca so o que o idempotente precisa; aqui a validacao e a real.
    monkeypatch.setattr(ws, "ensure_session_equipment", base_validator)
    monkeypatch.setattr(ws, "is_pair_coherent", lambda cur, mapping, eq: fake.coherent)
    return fake


base_validator = ws.ensure_session_equipment


def _codigo(exc):
    return exc.value.status_code, exc.value.detail["code"]


def test_equipamento_da_academia_e_coerente_cria(db):
    assert _add_exercise(equipment_id=2)["duplicate"] is False
    assert len(db.exercises) == 1


def test_equipamento_de_outra_academia_422(db):
    db.ge[2]["gym_id"] = 99
    with pytest.raises(HTTPException) as exc:
        _add_exercise(equipment_id=2)
    assert _codigo(exc) == (422, "equipment_not_in_session_gym")
    assert not db.exercises


def test_equipamento_inexistente_422(db):
    with pytest.raises(HTTPException) as exc:
        _add_exercise(equipment_id=555)
    assert _codigo(exc) == (422, "equipment_not_found")


def test_par_incoerente_422(db):
    db.coherent = False
    with pytest.raises(HTTPException) as exc:
        _add_exercise(equipment_id=2)
    assert _codigo(exc) == (422, "equipment_pair_incoherent")
    assert not db.exercises


def test_sessao_sem_academia_aceita_equipamento_coerente_e_loga(db):
    db.sessions[base.SESSION]["gym_id"] = None
    _add_exercise(equipment_id=2)
    assert len(db.exercises) == 1
    assert db.calls["log_event"] == 1


def test_sessao_sem_academia_equipamento_inexistente_422(db):
    db.sessions[base.SESSION]["gym_id"] = None
    with pytest.raises(HTTPException) as exc:
        _add_exercise(equipment_id=555)
    assert _codigo(exc) == (422, "equipment_not_found")


def test_sessao_sem_academia_par_incoerente_422(db):
    db.sessions[base.SESSION]["gym_id"] = None
    db.coherent = False
    with pytest.raises(HTTPException) as exc:
        _add_exercise(equipment_id=2)
    assert _codigo(exc) == (422, "equipment_pair_incoherent")
    assert not db.exercises


def test_inativo_da_propria_academia_cria_e_registra_log(db):
    db.ge[2]["is_active"] = 0
    _add_exercise(equipment_id=2)
    assert len(db.exercises) == 1
    assert db.calls["log_event"] == 1


def test_reenvio_com_a_mesma_chave_nao_revalida(db):
    _add_exercise(equipment_id=2, client_key="ex-1")
    db.ge.clear()  # a associacao sumiu: revalidar recusaria
    de_novo = _add_exercise(equipment_id=2, client_key="ex-1")
    assert de_novo["duplicate"] is True


def test_sem_equipment_id_nao_consulta_a_associacao(db):
    _add_exercise()
    assert not any("FROM gym_equipment" in " ".join(s.split()) for s in db.sql)
