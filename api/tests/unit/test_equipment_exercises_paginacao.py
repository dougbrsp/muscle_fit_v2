"""#246: GET /equipment-exercises aceita `limit`/`offset` opcionais (teto no limite);
sem eles o SQL e os parametros sao os de antes."""

from __future__ import annotations

import inspect

import pytest

from app import equipment_exercises as ee


class _Cur:
    def __init__(self, calls):
        self.calls = calls

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.calls.append((" ".join(sql.split()), tuple(params or ())))

    def fetchall(self):
        return []


class _Conn(_Cur):
    def cursor(self):
        return _Cur(self.calls)


@pytest.fixture
def chamadas(monkeypatch):
    calls: list = []
    monkeypatch.setattr(ee, "_connect", lambda: _Conn(calls))
    monkeypatch.setattr(ee, "load_convention_select_expr", lambda cursor: "NULL")
    return calls


def _sql_params(calls):
    return [c for c in calls if "FROM equipment_exercises eqex" in c[0]][-1]


def test_sem_parametros_nao_pagina(chamadas):
    assert ee.list_equipment_exercises(None, None, None, True, None, None) == {"items": []}
    sql, params = _sql_params(chamadas)
    assert "OFFSET" not in sql and params == ()


def test_limit_e_offset_viram_parametros(chamadas):
    ee.list_equipment_exercises(7, None, None, True, 50, 100)
    sql, params = _sql_params(chamadas)
    assert sql.endswith("LIMIT %s OFFSET %s") and params == (7, 50, 100)


def test_offset_sem_limit_usa_o_teto(chamadas):
    ee.list_equipment_exercises(None, None, None, True, None, 10)
    assert _sql_params(chamadas)[1] == (ee.EQUIPMENT_EXERCISES_MAX_LIMIT, 10)


def test_limites_declarados_na_validacao_da_rota():
    params = inspect.signature(ee.list_equipment_exercises).parameters
    limit = {type(m).__name__: m for m in params["limit"].default.metadata}
    assert (limit["Ge"].ge, limit["Le"].le) == (1, ee.EQUIPMENT_EXERCISES_MAX_LIMIT)
    assert params["offset"].default.metadata[0].ge == 0
