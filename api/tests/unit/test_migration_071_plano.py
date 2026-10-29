"""Plano da migracao 071 (aumento de carga pendente da trilha, Lote 3/P4 RPE 10)
contra um `information_schema` sintetico: ausente -> 2 ADD; convergido -> 0;
parcial -> so o que falta; tipo/nulabilidade divergente -> erro antes do
primeiro statement; tabela ausente -> erro (DRY-RUN aceita); `plan_down`."""

from __future__ import annotations

import functools

import pytest

from app import migrate as m

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "071_progression_track_pending_load.py"
T = "progression_tracks"


@functools.lru_cache(maxsize=1)
def _module() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


class FakeDB:
    def __init__(self, columns=None, *, table=True, dry_run=False):
        self.columns = dict(columns or {})
        self.table = table
        self.dry_run = dry_run
        self.executed: list[str] = []

    def query(self, sql, params=None):
        if "information_schema.TABLES" in sql:
            return [{"TABLE_NAME": T}] if self.table else []
        if "information_schema.COLUMNS" in sql:
            return [
                {"COLUMN_NAME": name, "COLUMN_TYPE": typ, "IS_NULLABLE": nul}
                for name, (typ, nul) in self.columns.items()
            ]
        raise AssertionError(f"consulta inesperada: {sql}")

    def execute(self, sql, params=None):
        self.executed.append(sql)


CONVERGIDO = {"pending_load_kg": ("decimal(6,2)", "YES"), "pending_load_since": ("datetime", "YES")}


def test_banco_atual_recebe_as_duas_colunas_null():
    statements = [sql for sql, _ in _module()["plan"](FakeDB())]
    assert statements == [_module()["ADD_LOAD_SQL"], _module()["ADD_SINCE_SQL"]]
    assert all("NULL DEFAULT NULL" in sql for sql in statements)


def test_convergido_nao_faz_nada_e_parcial_so_completa():
    assert _module()["plan"](FakeDB(CONVERGIDO)) == []
    parcial = {"pending_load_kg": ("DECIMAL(6, 2)", "YES")}
    assert [sql for sql, _ in _module()["plan"](FakeDB(parcial))] == [_module()["ADD_SINCE_SQL"]]


@pytest.mark.parametrize(
    "columns",
    [
        {"pending_load_kg": ("decimal(8,2)", "YES")},
        {"pending_load_since": ("datetime", "NO")},
    ],
)
def test_forma_divergente_falha_antes_do_primeiro_statement(columns):
    db = FakeDB(columns)
    with pytest.raises(_module()["TrackPendingLoadError"]):
        _module()["up"](db)
    assert db.executed == []


def test_tabela_ausente_falha_na_execucao_real_e_passa_no_dry_run():
    with pytest.raises(_module()["TrackPendingLoadError"]):
        _module()["plan"](FakeDB(table=False))
    assert len(_module()["plan"](FakeDB(table=False, dry_run=True))) == 2


def test_plan_down_solta_so_o_que_existe():
    assert [sql for sql, _ in _module()["plan_down"](FakeDB(CONVERGIDO))] == [
        _module()["DROP_SINCE_SQL"],
        _module()["DROP_LOAD_SQL"],
    ]
    assert _module()["plan_down"](FakeDB()) == []
    assert _module()["plan_down"](FakeDB(table=False)) == []
