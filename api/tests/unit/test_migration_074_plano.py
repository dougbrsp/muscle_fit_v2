"""Plano da migração 074 (antropometria em `users`, nutrição L0) contra um
`information_schema` sintético: ausente -> 2 ADD; convergido -> 0; parcial ->
só o que falta; tipo/nulabilidade divergente -> erro antes do primeiro
statement; tabela ausente -> erro (DRY-RUN aceita); `plan_down`.

`users.sex` não é tocado (o backfill para o vocabulário do motor fica para o
L2, junto com as telas de perfil, que hoje gravam 'M'/'F')."""

from __future__ import annotations

import functools

import pytest

from app import migrate as m

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "074_users_anthropometrics.py"
T = "users"


@functools.lru_cache(maxsize=1)
def _module() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


class FakeDB:
    def __init__(self, columns=None, *, table=True, dry_run=False):
        self.columns = dict(columns or {})
        self.table = table
        self.dry_run = dry_run
        self.executed: list[tuple[str, tuple | None]] = []

    def query(self, sql, params=None):
        if "information_schema.TABLES" in sql:
            return [{"TABLE_NAME": T}] if self.table else []
        if "information_schema.COLUMNS" in sql:
            wanted = set(params[1:])
            return [
                {"COLUMN_NAME": name, "COLUMN_TYPE": typ, "IS_NULLABLE": nul}
                for name, (typ, nul) in self.columns.items()
                if name in wanted
            ]
        raise AssertionError(f"consulta inesperada: {sql}")

    def execute(self, sql, params=None):
        self.executed.append((sql, params))


CONVERGIDO = {"height_cm": ("decimal(5,1)", "YES"), "activity_level": ("varchar(16)", "YES")}
# Colunas que já existem em `users` e não podem aparecer no plano.
EXISTENTES = {"sex": ("varchar(20)", "YES"), "birth_date": ("date", "YES")}


def _sqls(statements):
    return [sql for sql, _ in statements]


def test_ausente_recebe_as_duas_colunas_nulas():
    db = FakeDB(EXISTENTES)
    statements = _module()["plan"](db)
    assert _sqls(statements) == [_module()["ADD_HEIGHT_SQL"], _module()["ADD_ACTIVITY_SQL"]]
    assert all("NULL DEFAULT NULL" in sql for sql in _sqls(statements))
    assert all(params is None for _sql, params in statements)
    _module()["up"](db)
    assert _sqls(db.executed) == _sqls(statements)


def test_convergido_nao_faz_nada_e_parcial_so_completa():
    assert _module()["plan"](FakeDB({**EXISTENTES, **CONVERGIDO})) == []
    parcial = {"height_cm": ("DECIMAL(5,1)", "YES")}
    assert _sqls(_module()["plan"](FakeDB(parcial))) == [_module()["ADD_ACTIVITY_SQL"]]


def test_nao_toca_em_sex_nem_faz_dml():
    statements = _sqls(_module()["plan"](FakeDB(EXISTENTES))) + _sqls(_module()["plan_down"](FakeDB(CONVERGIDO)))
    assert not any("sex" in sql for sql in statements)
    # Todo SQL do módulo é DDL em `users` (nenhum UPDATE/INSERT/DELETE).
    sqls = {name: value for name, value in _module().items() if name.endswith("_SQL")}
    assert set(sqls) == {"ADD_HEIGHT_SQL", "ADD_ACTIVITY_SQL", "DROP_HEIGHT_SQL", "DROP_ACTIVITY_SQL"}
    assert all(sql.startswith(f"ALTER TABLE {T} ") for sql in sqls.values())


@pytest.mark.parametrize(
    "columns",
    [
        {"height_cm": ("decimal(6,2)", "YES")},
        {"activity_level": ("varchar(16)", "NO")},
        {"activity_level": ("int", "YES")},
    ],
)
def test_forma_divergente_falha_antes_do_primeiro_statement(columns):
    db = FakeDB(columns)
    with pytest.raises(_module()["UsersAnthropometricsError"]):
        _module()["up"](db)
    assert db.executed == []


def test_tabela_ausente_falha_na_execucao_real_e_passa_no_dry_run():
    with pytest.raises(_module()["UsersAnthropometricsError"]):
        _module()["plan"](FakeDB(table=False))
    assert _sqls(_module()["plan"](FakeDB(table=False, dry_run=True))) == [
        _module()["ADD_HEIGHT_SQL"],
        _module()["ADD_ACTIVITY_SQL"],
    ]


def test_plan_down_solta_so_o_que_existe():
    assert _sqls(_module()["plan_down"](FakeDB(CONVERGIDO))) == [
        _module()["DROP_ACTIVITY_SQL"],
        _module()["DROP_HEIGHT_SQL"],
    ]
    assert _sqls(_module()["plan_down"](FakeDB({"height_cm": ("decimal(5,1)", "YES")}))) == [
        _module()["DROP_HEIGHT_SQL"]
    ]
    assert _module()["plan_down"](FakeDB(EXISTENTES)) == []
    assert _module()["plan_down"](FakeDB(table=False)) == []
