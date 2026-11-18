"""Plano da migracao 120 (#196): `form_practice` em exercises.
Sem MySQL: FakeDB le um `information_schema` em memoria."""

from __future__ import annotations

import functools

import pytest

from app import migrate as m

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "120_form_practice.py"
ADD = "ALTER TABLE exercises ADD COLUMN form_practice TINYINT(1) NOT NULL DEFAULT 0, ALGORITHM=INSTANT"
DROP = "ALTER TABLE exercises DROP COLUMN form_practice"
OK = ("tinyint(1)", "NO", "0")


@functools.lru_cache(maxsize=1)
def _mod() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


class FakeDB:
    def __init__(self, *, tabela=True, coluna=None, dry_run=False):
        self.tabela = tabela
        self.coluna = coluna
        self.dry_run = dry_run
        self.executed: list[str] = []

    def query(self, sql, params=None):
        assert ":" not in sql
        if "information_schema.TABLES" in sql:
            return [{"TABLE_NAME": "exercises"}] if self.tabela else []
        if "information_schema.COLUMNS" in sql:
            assert params == ("exercises", "form_practice")
            if not self.tabela or self.coluna is None:
                return []
            tipo, nul, default = self.coluna
            return [
                {
                    "TABLE_NAME": "exercises",
                    "COLUMN_TYPE": tipo,
                    "IS_NULLABLE": nul,
                    "COLUMN_DEFAULT": default,
                }
            ]
        raise AssertionError(f"leitura inesperada: {sql}")

    def execute(self, sql, params=None):
        assert params is None
        self.executed.append(sql)


def test_banco_atual_recebe_a_coluna():
    db = FakeDB()
    _mod()["up"](db)
    assert db.executed == [ADD]


def test_convergido_nao_faz_nada():
    db = FakeDB(coluna=OK)
    _mod()["up"](db)
    assert db.executed == []


@pytest.mark.parametrize(
    "coluna",
    [
        ("int", "NO", "0"),
        ("tinyint(1)", "YES", "0"),
        ("tinyint(1)", "NO", "1"),
        ("tinyint(1)", "NO", None),
    ],
)
def test_coluna_divergente_falha_antes_do_primeiro_statement(coluna):
    db = FakeDB(coluna=coluna)
    with pytest.raises(Exception, match="120"):
        _mod()["up"](db)
    assert db.executed == []


def test_tabela_ausente_falha_e_dry_run_lista():
    with pytest.raises(Exception, match="120"):
        _mod()["plan"](FakeDB(tabela=False))
    assert _mod()["plan"](FakeDB(tabela=False, dry_run=True)) == [(ADD, None)]


def test_down_solta_a_coluna_e_e_idempotente():
    db = FakeDB(coluna=OK)
    _mod()["down"](db)
    assert db.executed == [DROP]
    db2 = FakeDB()
    _mod()["down"](db2)
    assert db2.executed == []
