"""Plano da migracao 043 (`password_reset_tokens` + `password_reset_attempts`,
melhoria M5) sobre um recorte de `information_schema` construido no teste.

- banco so com o baseline            -> exatamente 2 `CREATE TABLE`;
- banco com as duas tabelas          -> 0 statements (convergido);
- so uma das tabelas presente        -> 1 `CREATE TABLE` (a que falta);
- `users` ausente/incompativel       -> erro inclusive em dry-run (baseline);
- tabela presente em outro formato   -> erro ANTES de qualquer statement.

Rodar: cd api && python -m pytest tests/unit/test_migration_043_password_reset.py
"""

from __future__ import annotations

import copy

import pytest

from app import migrate as m

MIGRATION_PATH = m.DEFAULT_MIGRATIONS_DIR / "043_password_reset_tokens.py"
MODULE = m.load_python_migration(MIGRATION_PATH).__globals__

TOKENS = MODULE["TOKENS_TABLE"]
ATTEMPTS = MODULE["ATTEMPTS_TABLE"]
CREATE_TOKENS_SQL = MODULE["CREATE_TOKENS_SQL"]
CREATE_ATTEMPTS_SQL = MODULE["CREATE_ATTEMPTS_SQL"]
Erro = MODULE["PasswordResetTokensError"]


class DbFalso:
    def __init__(self, snapshot, dry_run: bool = False):
        self.snapshot = snapshot
        self.dry_run = dry_run
        self.escritas: list = []

    def query(self, sql, params=None):
        if "information_schema.TABLES" in sql:
            return [{"TABLE_NAME": name, "ENGINE": engine} for name, engine in self.snapshot["tables"].items()]
        return [
            {
                "TABLE_NAME": table,
                "COLUMN_NAME": column,
                "COLUMN_TYPE": info["COLUMN_TYPE"],
                "IS_NULLABLE": info["IS_NULLABLE"],
                "COLUMN_KEY": info.get("COLUMN_KEY", ""),
            }
            for (table, column), info in self.snapshot["columns"].items()
        ]

    def execute(self, sql, params=None):
        self.escritas.append((sql, params))


def _baseline() -> dict:
    return {
        "tables": {"users": "InnoDB"},
        "columns": {("users", "id"): {"COLUMN_TYPE": "bigint", "IS_NULLABLE": "NO", "COLUMN_KEY": "PRI"}},
    }


def _apos_043() -> dict:
    snapshot = _baseline()
    for table, (_, expected) in MODULE["TARGETS"].items():
        snapshot["tables"][table] = "InnoDB"
        for column, (column_type, nullable) in expected.items():
            snapshot["columns"][(table, column)] = {"COLUMN_TYPE": column_type, "IS_NULLABLE": nullable}
    return snapshot


def test_banco_baseline_produz_os_dois_creates():
    plano = MODULE["plan"](DbFalso(_baseline()))
    assert plano == [(CREATE_TOKENS_SQL, None), (CREATE_ATTEMPTS_SQL, None)]
    assert "token_hash CHAR(64) NOT NULL" in CREATE_TOKENS_SQL
    assert "UNIQUE KEY uq_prt_token_hash (token_hash)" in CREATE_TOKENS_SQL
    assert "REFERENCES users(id) ON DELETE CASCADE" in CREATE_TOKENS_SQL
    assert "KEY idx_pra_scope_subject_created (scope, subject_hash, created_at)" in CREATE_ATTEMPTS_SQL


def test_migracao_e_reexecutavel_sem_escrever_nada():
    db = DbFalso(_apos_043())
    assert MODULE["plan"](db) == []
    MODULE["up"](db)
    assert db.escritas == []


def test_up_executa_os_creates_uma_vez():
    db = DbFalso(_baseline())
    MODULE["up"](db)
    assert db.escritas == [(CREATE_TOKENS_SQL, None), (CREATE_ATTEMPTS_SQL, None)]


def test_so_a_tabela_ausente_entra_no_plano():
    snapshot = _apos_043()
    del snapshot["tables"][ATTEMPTS]
    snapshot["columns"] = {key: value for key, value in snapshot["columns"].items() if key[0] != ATTEMPTS}
    assert MODULE["plan"](DbFalso(snapshot)) == [(CREATE_ATTEMPTS_SQL, None)]


def test_users_ausente_falha_MESMO_em_dry_run():
    for dry_run in (False, True):
        db = DbFalso({"tables": {}, "columns": {}}, dry_run=dry_run)
        with pytest.raises(Erro, match="users nao existe"):
            MODULE["plan"](db)
        assert db.escritas == []


@pytest.mark.parametrize(
    "alteracao, trecho",
    [
        ({"COLUMN_TYPE": "int"}, "esperado bigint"),
        ({"COLUMN_KEY": ""}, "PRIMARY KEY"),
    ],
)
def test_users_id_incompativel_falha(alteracao, trecho):
    snapshot = _baseline()
    snapshot["columns"][("users", "id")].update(alteracao)
    with pytest.raises(Erro, match=trecho):
        MODULE["plan"](DbFalso(snapshot))


def test_engine_diferente_de_innodb_falha():
    snapshot = _apos_043()
    snapshot["tables"][TOKENS] = "MyISAM"
    with pytest.raises(Erro, match="ENGINE"):
        MODULE["plan"](DbFalso(snapshot))


@pytest.mark.parametrize(
    "coluna, alteracao, trecho",
    [
        ("token_hash", {"COLUMN_TYPE": "varchar(64)"}, r"esperado char\(64\)"),
        ("used_at", {"IS_NULLABLE": "NO"}, "IS_NULLABLE"),
        ("expires_at", {"COLUMN_TYPE": "timestamp"}, "esperado datetime"),
    ],
)
def test_tokens_em_outro_formato_falha_antes_de_qualquer_statement(coluna, alteracao, trecho):
    snapshot = _apos_043()
    info = copy.deepcopy(snapshot["columns"][(TOKENS, coluna)])
    info.update(alteracao)
    snapshot["columns"][(TOKENS, coluna)] = info
    db = DbFalso(snapshot)
    with pytest.raises(Erro, match=trecho):
        MODULE["plan"](db)
    assert db.escritas == []


def test_coluna_faltando_na_tabela_existente_falha():
    snapshot = _apos_043()
    del snapshot["columns"][(ATTEMPTS, "subject_hash")]
    with pytest.raises(Erro, match="subject_hash nao existe"):
        MODULE["plan"](DbFalso(snapshot))
