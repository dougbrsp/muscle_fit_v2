"""Plano da migração 099 (exercício laboral, M100) contra um
`information_schema` sintético:

- banco atual -> 1 ALTER INSTANT com as 4 colunas, na ordem;
- convergido -> 0 (inclusive `int(11)` de MySQL antigo); meio caminho -> só o que falta;
- tipo/nulabilidade/default divergente -> erro antes do 1º statement;
- tabela ausente -> erro (DRY-RUN aceita); `plan_down` inverso;
- DDL: nenhuma FK, nenhum backfill, nenhum ':' em literal (Data API).

Rodar: cd api && python -m pytest tests/unit/test_migration_099_plano.py
"""

from __future__ import annotations

import functools

import pytest

from app import migrate as m

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "099_exercicio_laboral.py"


@functools.lru_cache(maxsize=1)
def _mod() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


def _forma_ok():
    return {name: (tipo, nul, default) for name, _ddl, tipo, nul, default in _mod()["COLUMNS"]}


class FakeDB:
    def __init__(self, *, existe=True, colunas=None, dry_run=False):
        self.existe = existe
        self.colunas = {} if colunas is None else colunas  # nome -> (tipo, nullable, default)
        self.dry_run = dry_run
        self.executed: list[str] = []

    def query(self, sql, params=None):
        if "information_schema.TABLES" in sql:
            assert params == ("exercise_catalog",)
            return [{"TABLE_NAME": "exercise_catalog"}] if self.existe else []
        if "information_schema.COLUMNS" in sql:
            assert params[0] == "exercise_catalog"
            return [
                {"COLUMN_NAME": c, "COLUMN_TYPE": tipo, "IS_NULLABLE": nul, "COLUMN_DEFAULT": default}
                for c, (tipo, nul, default) in self.colunas.items()
                if c in params[1:]
            ]
        raise AssertionError(f"leitura inesperada: {sql}")

    def execute(self, sql, params=None):
        self.executed.append(sql)


def test_banco_atual_recebe_um_alter_instant():
    db = FakeDB()
    _mod()["up"](db)
    assert db.executed == [
        "ALTER TABLE exercise_catalog ADD COLUMN is_laboral TINYINT(1) NOT NULL DEFAULT 0, "
        "ADD COLUMN laboral_default_sets INT NULL DEFAULT NULL, "
        "ADD COLUMN laboral_default_reps INT NULL DEFAULT NULL, "
        "ADD COLUMN laboral_default_load_kg DECIMAL(8,2) NULL DEFAULT NULL, ALGORITHM=INSTANT"
    ]


def test_convergido_nao_faz_nada():
    assert _mod()["plan"](FakeDB(colunas=_forma_ok())) == []
    antigo = {k: ("int(11)" if t == "int" else t.upper(), n.lower(), d) for k, (t, n, d) in _forma_ok().items()}
    assert _mod()["plan"](FakeDB(colunas=antigo)) == []


def test_meio_caminho_aplica_so_o_que_falta():
    colunas = _forma_ok()
    del colunas["laboral_default_load_kg"]
    assert [s for s, _ in _mod()["plan"](FakeDB(colunas=colunas))] == [
        "ALTER TABLE exercise_catalog ADD COLUMN laboral_default_load_kg DECIMAL(8,2) NULL DEFAULT NULL, ALGORITHM=INSTANT"
    ]


@pytest.mark.parametrize(
    "coluna, forma",
    [
        ("is_laboral", ("int", "NO", "0")),
        ("is_laboral", ("tinyint(1)", "YES", "0")),
        ("is_laboral", ("tinyint(1)", "NO", "1")),
        ("laboral_default_sets", ("bigint", "YES", None)),
        ("laboral_default_reps", ("int", "NO", None)),
        ("laboral_default_load_kg", ("decimal(6,2)", "YES", None)),
        ("laboral_default_load_kg", ("decimal(8,2)", "YES", "0.00")),
    ],
)
def test_forma_divergente_falha_antes_do_primeiro_statement(coluna, forma):
    db = FakeDB(colunas={coluna: forma})
    with pytest.raises(_mod()["ExercicioLaboralError"], match="Nenhum statement executado"):
        _mod()["up"](db)
    assert db.executed == []


def test_tabela_ausente_falha_na_execucao_real_e_passa_no_dry_run():
    with pytest.raises(_mod()["ExercicioLaboralError"], match="nao existe"):
        _mod()["plan"](FakeDB(existe=False))
    assert len(_mod()["plan"](FakeDB(existe=False, dry_run=True))) == 1


def test_plan_down_inverso_e_idempotente():
    assert [s for s, _ in _mod()["plan_down"](FakeDB(colunas=_forma_ok()))] == [
        "ALTER TABLE exercise_catalog DROP COLUMN is_laboral, DROP COLUMN laboral_default_sets, "
        "DROP COLUMN laboral_default_reps, DROP COLUMN laboral_default_load_kg"
    ]
    assert _mod()["plan_down"](FakeDB()) == []


def test_ddl_sem_fk_sem_backfill_sem_dois_pontos():
    sql = _mod()["add_sql"](_mod()["NAMES"])
    assert "REFERENCES" not in sql.upper() and "FOREIGN" not in sql.upper()
    assert "UPDATE" not in sql.upper() and ":" not in sql
