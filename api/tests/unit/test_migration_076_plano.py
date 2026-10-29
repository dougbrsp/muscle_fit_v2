"""Plano da migração 076 (`activities.calories_source`, nutrição L0) contra um
`information_schema` sintético: ausente -> 1 ADD; convergido -> 0;
tipo/nulabilidade divergente -> erro antes do primeiro statement; tabela
ausente -> erro (DRY-RUN aceita); `plan_down`; o vocabulário cabe no VARCHAR(16)."""

from __future__ import annotations

import functools

import pytest

from app import migrate as m
from app.domain import calories_source as cs

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "076_activities_calories_source.py"
T = "activities"


@functools.lru_cache(maxsize=1)
def _module() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


class FakeDB:
    def __init__(self, column=None, *, table=True, dry_run=False):
        self.column = column  # (tipo, IS_NULLABLE) ou None
        self.table = table
        self.dry_run = dry_run
        self.executed: list[str] = []

    def query(self, sql, params=None):
        if "information_schema.TABLES" in sql:
            return [{"TABLE_NAME": T}] if self.table else []
        if "information_schema.COLUMNS" in sql:
            if self.column is None:
                return []
            typ, nul = self.column
            return [{"COLUMN_NAME": "calories_source", "COLUMN_TYPE": typ, "IS_NULLABLE": nul}]
        raise AssertionError(f"consulta inesperada: {sql}")

    def execute(self, sql, params=None):
        self.executed.append(sql)


def test_banco_atual_recebe_a_coluna_null_sem_backfill():
    db = FakeDB()
    assert [sql for sql, _ in _module()["plan"](db)] == [_module()["ADD_SQL"]]
    assert "NULL DEFAULT NULL" in _module()["ADD_SQL"]
    fonte = MIGRATION.read_text(encoding="utf-8").upper()
    for proibido in ("UPDATE ", "INSERT ", "DELETE "):
        assert proibido not in fonte


def test_convergido_nao_faz_nada():
    assert _module()["plan"](FakeDB(("varchar(16)", "YES"))) == []
    assert _module()["plan"](FakeDB(("VARCHAR(16)", "YES"))) == []


@pytest.mark.parametrize("column", [("varchar(32)", "YES"), ("varchar(16)", "NO"), ("int", "YES")])
def test_forma_divergente_falha_antes_do_primeiro_statement(column):
    db = FakeDB(column)
    with pytest.raises(_module()["ActivitiesCaloriesSourceError"]):
        _module()["up"](db)
    assert db.executed == []


def test_tabela_ausente_falha_na_execucao_real_e_passa_no_dry_run():
    with pytest.raises(_module()["ActivitiesCaloriesSourceError"]):
        _module()["plan"](FakeDB(table=False))
    assert [sql for sql, _ in _module()["plan"](FakeDB(table=False, dry_run=True))] == [_module()["ADD_SQL"]]


def test_plan_down_solta_so_se_existe():
    assert [sql for sql, _ in _module()["plan_down"](FakeDB(("varchar(16)", "YES")))] == [_module()["DROP_SQL"]]
    assert _module()["plan_down"](FakeDB()) == []
    assert _module()["plan_down"](FakeDB(table=False)) == []


def test_vocabulario_do_dono_cabe_na_coluna():
    assert all(len(code) <= 16 for code in cs.CALORIES_SOURCES)
    assert set(cs.CALORIES_SOURCES) == {"manual", "intervals"}
