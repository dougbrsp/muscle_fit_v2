"""GET /gym-equipment devolve `load_convention` (#163), pela expressao do dono
(`load_conventions`); NULL vale a convencao padrao do dono."""

from __future__ import annotations

from app import gyms
from app.load_conventions import DEFAULT_LOAD_CONVENTION


class _Cur:
    def __init__(self, rows):
        self.rows = rows
        self.sql = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self

    def execute(self, sql, params=None):
        self.sql = sql

    def fetchall(self):
        return [dict(r) for r in self.rows]


def _listar(monkeypatch, rows):
    cur = _Cur(rows)
    monkeypatch.setattr(gyms, "_connect", lambda: cur)
    monkeypatch.setattr(gyms, "load_convention_select_expr", lambda cursor: "eqex.load_convention")
    monkeypatch.setattr(gyms, "permissions_for_request", lambda request, identity: None)
    monkeypatch.setattr(gyms, "manufacturer_editor", lambda identity, perms: False)
    out = gyms.list_gym_equipment(
        request=None, gym_id=3, mapping_id=None, primary_group=None, active_only=True, identity=None
    )
    return cur, out["items"]


def _row(convention, **extra):
    return {"id": 7, "gym_id": 3, "equipment_id": 2, "equipment_name": "Smith", "is_active": 1,
            "load_convention": convention, **extra}


def test_resposta_traz_a_convencao_e_o_sql_usa_o_join_do_dono(monkeypatch):
    cur, items = _listar(monkeypatch, [_row("per_side")])
    assert items[0]["load_convention"] == "per_side"
    assert "eqex.load_convention AS load_convention" in cur.sql
    assert "LEFT JOIN equipment_exercises eqex" in cur.sql


def test_convencao_nula_vira_a_padrao_do_dono(monkeypatch):
    _, items = _listar(monkeypatch, [_row(None)])
    assert items[0]["load_convention"] == DEFAULT_LOAD_CONVENTION.value


def test_campos_existentes_nao_mudam(monkeypatch):
    _, items = _listar(monkeypatch, [_row("total")])
    item = items[0]
    assert (item["id"], item["gym_id"], item["equipment_id"], item["equipment_name"], item["is_active"]) == (
        7, 3, 2, "Smith", 1,
    )
