"""Plano da migração 087 (`client_key` em `exercises`/`exercise_sets`,
Evolução ZEPP PR C) contra um `information_schema` sintético:

- banco atual -> 4 statements (ADD COLUMN e ADD UNIQUE separados, por tabela);
- convergido -> 0; meio caminho -> só o que falta;
- tipo/nulabilidade/collation/índice divergente -> erro antes do 1º statement;
- tabela ausente -> erro (DRY-RUN aceita); `plan_down` inverso;
- DDL: coluna NULL sem default/backfill, ascii_bin, nenhuma FK, nenhum ':'
  em literal (Data API).

Rodar: cd api && python -m pytest tests/unit/test_migration_087_plano.py
"""

from __future__ import annotations

import functools
import re

import pytest

from app import migrate as m

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "087_client_keys_sessao_ao_vivo.py"
TABELAS = ("exercises", "exercise_sets")


@functools.lru_cache(maxsize=1)
def _mod() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


def _coluna_ok():
    return ("varchar(64)", "YES", "ascii_bin")


def _indice_ok(tabela):
    nome, cols = _mod()["TARGETS"][tabela]
    return nome, (0, cols)


class FakeDB:
    def __init__(self, *, tabelas=TABELAS, colunas=None, indices=None, dry_run=False):
        self.tabelas = set(tabelas)
        self.colunas = {} if colunas is None else colunas  # tabela -> (tipo, nullable, collation)
        self.indices = {} if indices is None else indices  # tabela -> (nome, (non_unique, cols))
        self.dry_run = dry_run
        self.executed: list[str] = []

    def query(self, sql, params=None):
        if "information_schema.TABLES" in sql:
            return [{"TABLE_NAME": t} for t in TABELAS if t in self.tabelas]
        if "information_schema.COLUMNS" in sql:
            assert params[-1] == "client_key"
            return [
                {"TABLE_NAME": t, "COLUMN_NAME": "client_key", "COLUMN_TYPE": tipo, "IS_NULLABLE": nul,
                 "COLLATION_NAME": coll}
                for t, (tipo, nul, coll) in self.colunas.items()
            ]
        if "information_schema.STATISTICS" in sql:
            return [
                {"TABLE_NAME": t, "INDEX_NAME": nome, "SEQ_IN_INDEX": seq, "COLUMN_NAME": col, "NON_UNIQUE": nu}
                for t, (nome, (nu, cols)) in self.indices.items()
                for seq, col in enumerate(cols, 1)
            ]
        raise AssertionError(f"leitura inesperada: {sql}")

    def execute(self, sql, params=None):
        self.executed.append(sql)


def _convergido(**kw):
    return FakeDB(colunas={t: _coluna_ok() for t in TABELAS}, indices={t: _indice_ok(t) for t in TABELAS}, **kw)


def test_banco_atual_recebe_4_statements_na_ordem():
    db = FakeDB()
    _mod()["up"](db)
    assert db.executed == [
        "ALTER TABLE exercises ADD COLUMN client_key VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL DEFAULT NULL, ALGORITHM=INSTANT",
        "ALTER TABLE exercises ADD UNIQUE KEY uq_exercises_activity_client_key (activity_id, client_key), ALGORITHM=INPLACE, LOCK=NONE",
        "ALTER TABLE exercise_sets ADD COLUMN client_key VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL DEFAULT NULL, ALGORITHM=INSTANT",
        "ALTER TABLE exercise_sets ADD UNIQUE KEY uq_exercise_sets_exercise_client_key (exercise_id, client_key), ALGORITHM=INPLACE, LOCK=NONE",
    ]


def test_convergido_nao_faz_nada():
    assert _mod()["plan"](_convergido()) == []
    maiusculo = _convergido()
    maiusculo.colunas = {t: ("VARCHAR(64)", "YES", "ASCII_BIN") for t in TABELAS}
    assert _mod()["plan"](maiusculo) == []


def test_meio_caminho_aplica_so_o_que_falta():
    db = FakeDB(colunas={"exercises": _coluna_ok(), "exercise_sets": _coluna_ok()},
                indices={"exercises": _indice_ok("exercises")})
    assert [s for s, _ in _mod()["plan"](db)] == [_mod()["add_unique_sql"]("exercise_sets")]


@pytest.mark.parametrize(
    "ajuste",
    [
        {"colunas": {"exercises": ("varchar(32)", "YES", "ascii_bin")}},
        {"colunas": {"exercises": ("varchar(64)", "NO", "ascii_bin")}},
        {"colunas": {"exercise_sets": ("varchar(64)", "YES", "utf8mb4_0900_ai_ci")}},
        {"indices": {"exercises": ("uq_exercises_activity_client_key", (0, ("client_key",)))}},
        {"indices": {"exercise_sets": ("uq_exercise_sets_exercise_client_key", (1, ("exercise_id", "client_key")))}},
    ],
)
def test_forma_divergente_falha_antes_do_primeiro_statement(ajuste):
    db = FakeDB(**ajuste)
    with pytest.raises(_mod()["ClientKeysError"], match="Nenhum statement executado"):
        _mod()["up"](db)
    assert db.executed == []


def test_tabela_ausente_falha_na_execucao_real_e_passa_no_dry_run():
    with pytest.raises(_mod()["ClientKeysError"]):
        _mod()["plan"](FakeDB(tabelas=("exercises",)))
    assert len(_mod()["plan"](FakeDB(tabelas=(), dry_run=True))) == 4


def test_plan_down_inverso_e_idempotente():
    assert [s for s, _ in _mod()["plan_down"](_convergido())] == [
        "ALTER TABLE exercises DROP INDEX uq_exercises_activity_client_key",
        "ALTER TABLE exercises DROP COLUMN client_key",
        "ALTER TABLE exercise_sets DROP INDEX uq_exercise_sets_exercise_client_key",
        "ALTER TABLE exercise_sets DROP COLUMN client_key",
    ]
    assert _mod()["plan_down"](FakeDB()) == []
    assert _mod()["plan_down"](FakeDB(tabelas=())) == []


def test_ddl_sem_fk_sem_backfill_e_sem_dois_pontos_em_literal():
    fonte = MIGRATION.read_text(encoding="utf-8")
    statements = [s for s, _ in _mod()["plan"](FakeDB())] + [s for s, _ in _mod()["plan_down"](_convergido())]
    for sql in statements:
        assert "FOREIGN KEY" not in sql.upper() and "REFERENCES" not in sql.upper()
        assert ":" not in sql
    codigo = fonte.split('"""', 2)[2].upper()  # sem a docstring
    for proibido in ("UPDATE ", "INSERT ", "DELETE ", "EXERCISE_CATALOG", "PREPARE ", "SET @"):
        assert proibido not in codigo
    assert re.search(r"NULL DEFAULT NULL", _mod()["COLUMN_DDL"])
