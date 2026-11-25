"""#244: Importacao (POST /activities) e Registro (PUT /exercises/logs/{id}) validam o
`equipment_id` explicito pelo MESMO helper do Modo Treino (`ensure_session_equipment`),
com o mesmo 422 tipado. Equipamento igual ao ja gravado nao revalida (registros
antigos de par incoerente seguem aceitos); atividade sem academia aceita e loga."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app import services
from tests.unit import test_reimportacao_preserva_marca_e_objetivo as reimp

GYM = 3


class _Cur:
    """Responde as consultas reais de `ensure_session_equipment` + `equipment_pair`."""

    def __init__(self, ge=None, serves=None):
        self.ge = {2: {"gym_id": GYM, "is_active": 1}} if ge is None else ge
        self.serves = {2: 11} if serves is None else serves
        self.queries = 0
        self._one = None
        self._all: list = []

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.queries += 1
        if flat.startswith(("SELECT gym_id, is_active FROM gym_equipment", "SELECT ge.gym_id, ge.is_active FROM gym_equipment ge JOIN gyms")):
            self._one = self.ge.get(params[0])
        elif flat.startswith("SELECT ge.id, COALESCE"):
            self._all = [{"id": i, "exercise_id": self.serves[i]} for i in params if i in self.serves]
        else:
            self._all = []

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._all


def _codigo(exc):
    return exc.value.status_code, exc.value.detail["code"]


def test_equipamento_coerente_da_academia_passa():
    services.validate_explicit_equipment(_Cur(), GYM, 11, 2)


def test_equipamento_inexistente_422():
    with pytest.raises(HTTPException) as exc:
        services.validate_explicit_equipment(_Cur(), GYM, 11, 555)
    assert _codigo(exc) == (422, "equipment_not_in_session_gym")


def test_equipamento_de_outra_academia_422():
    with pytest.raises(HTTPException) as exc:
        services.validate_explicit_equipment(_Cur(), 99, 11, 2)
    assert _codigo(exc) == (422, "equipment_not_in_session_gym")


def test_par_incoerente_422_mas_igual_ao_gravado_passa():
    cur = _Cur(serves={2: 99})
    with pytest.raises(HTTPException) as exc:
        services.validate_explicit_equipment(cur, GYM, 11, 2)
    assert _codigo(exc) == (422, "equipment_pair_incoherent")
    cur.queries = 0
    services.validate_explicit_equipment(cur, GYM, 11, 2, previous_equipment_id=2)  # registro antigo, sem mudanca
    assert cur.queries == 0


def test_sem_academia_aceita_equipamento_existente_e_coerente():
    services.validate_explicit_equipment(_Cur(), None, 11, 2)


def test_sem_equipment_id_nao_consulta():
    cur = _Cur()
    services.validate_explicit_equipment(cur, GYM, 11, None)
    assert cur.queries == 0


def test_importacao_passa_o_equipamento_gravado_da_reimportacao(monkeypatch):
    validated: list = []
    previous = [{"sequence": 1, "original_name": "Supino", "equipment_id": 2, "form_practice": 0, "training_goal": None}]
    # `_run` reaplica seus fakes de gym; o que importa e o argumento `previous_equipment_id`.
    monkeypatch.setattr(services, "validate_explicit_equipment", lambda cur, g, m, eq, prev=None: validated.append((eq, prev)))
    ex = reimp._ex(1, "Supino")
    ex["equipment_id"] = 2
    reimp._run(monkeypatch, previous, [ex])
    assert validated == [(2, 2)]


def test_importacao_nova_valida_sem_equipamento_anterior(monkeypatch):
    validated: list = []
    monkeypatch.setattr(services, "validate_explicit_equipment", lambda cur, g, m, eq, prev=None: validated.append((eq, prev)))
    ex = reimp._ex(1, "Supino")
    ex["equipment_id"] = 2
    reimp._run(monkeypatch, [], [ex])
    assert validated == [(2, None)]


class _Parada(Exception):
    """Sentinela: a validacao passou e o fluxo chegou em `resolve_equipment_id`."""


class _CurRegistro(_Cur):
    def __init__(self, gravado, **kw):
        super().__init__(**kw)
        self.gravado = gravado

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        if flat.startswith("SELECT id, activity_id"):
            self._one = {"id": 7, "activity_id": 5, "original_name": "Supino", "mapping_id": 11, "equipment_id": self.gravado, "activity_gym_id": GYM}
        elif "FROM exercise_sets" in flat:
            self._all = []
        else:
            super().execute(sql, params)


def _registro(monkeypatch, cur, equipment_id):
    def _parar(*a, **k):
        raise _Parada

    monkeypatch.setattr(services, "resolve_equipment_id", _parar)
    services._update_exercise_log_tx(None, cur, 7, {"equipment_id": equipment_id, "sets": []}, None)


def test_registro_equipamento_igual_ao_gravado_nao_valida(monkeypatch):
    cur = _CurRegistro(gravado=2, serves={2: 99})  # par incoerente, mas inalterado
    with pytest.raises(_Parada):
        _registro(monkeypatch, cur, 2)
    assert cur.queries == 0  # nenhuma consulta de gym_equipment


def test_registro_equipamento_diferente_e_incoerente_422(monkeypatch):
    cur = _CurRegistro(gravado=1, serves={2: 99})
    with pytest.raises(HTTPException) as exc:
        _registro(monkeypatch, cur, 2)
    assert _codigo(exc) == (422, "equipment_pair_incoherent")
