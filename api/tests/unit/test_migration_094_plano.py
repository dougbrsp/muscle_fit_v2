"""Plano da migração 094 (deload, M97 D).

- Banco atual (sem a tabela e sem a coluna) -> 2 statements (CREATE + ADD COLUMN INSTANT).
- Convergido -> 0; forma divergente -> erro antes do primeiro statement.
- ENUM da coluna = `DELOAD_SOURCE_VALUES` do dono; FK só para `users`.
- `down` = DROP COLUMN + DROP TABLE.

Rodar: cd api && python -m pytest tests/unit/test_migration_094_plano.py
"""

from __future__ import annotations

import functools
import re

import pytest

from app import migrate as m
from app.domain import deload as dl

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "094_deload.py"
T = "deload_periods"


@functools.lru_cache(maxsize=1)
def _module() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


def _sql(texto: str) -> str:
    return " ".join(texto.split())


class FakeDB:
    def __init__(self, *, tabela=False, users=True, activities=True, coluna=None, colunas=None, indice=None,
                 dry_run=False):
        self.tabela = tabela
        self.users = users
        self.activities = activities
        self.coluna = coluna  # (COLUMN_TYPE, IS_NULLABLE) de activities.deload_source; None = ausente
        self.colunas = dict(_module()["EXPECTED_COLUMNS"] if colunas is None else colunas)
        self.indice = _module()["EXPECTED_INDEX_COLUMNS"] if indice is None else indice
        self.dry_run = dry_run

    def query(self, sql, params=None):
        if "information_schema.TABLES" in sql:
            return (
                ([{"TABLE_NAME": T, "ENGINE": "InnoDB"}] if self.tabela else [])
                + ([{"TABLE_NAME": "users", "ENGINE": "InnoDB"}] if self.users else [])
                + ([{"TABLE_NAME": "activities", "ENGINE": "InnoDB"}] if self.activities else [])
            )
        if "information_schema.COLUMNS" in sql:
            linhas = [{"TABLE_NAME": "users", "COLUMN_NAME": "id", "COLUMN_TYPE": "bigint", "IS_NULLABLE": "NO",
                       "COLUMN_KEY": "PRI"}] if self.users else []
            if self.tabela:
                linhas += [{"TABLE_NAME": T, "COLUMN_NAME": n, "COLUMN_TYPE": t, "IS_NULLABLE": nu, "COLUMN_KEY": ""}
                           for n, (t, nu) in self.colunas.items()]
            if self.activities and self.coluna is not None:
                linhas.append({"TABLE_NAME": "activities", "COLUMN_NAME": "deload_source",
                               "COLUMN_TYPE": self.coluna[0], "IS_NULLABLE": self.coluna[1], "COLUMN_KEY": ""})
            return linhas
        if "information_schema.STATISTICS" in sql:
            if not self.tabela:
                return []
            return [{"INDEX_NAME": "idx_dp_user_period", "SEQ_IN_INDEX": seq, "COLUMN_NAME": col}
                    for seq, col in enumerate(self.indice, 1)]
        raise AssertionError(f"leitura inesperada: {sql}")

    def execute(self, sql, params=None):
        raise AssertionError(f"escrita no plano: {sql}")


CONVERGIDO = {"tabela": True, "coluna": ("enum('period','manual')", "YES")}


def test_enum_da_migracao_e_o_vocabulario_do_dono():
    assert _module()["SOURCE_VALUES"] == dl.DELOAD_SOURCE_VALUES
    assert _module()["COLUMN"] == dl.DELOAD_SOURCE_COLUMN


def test_banco_atual_cria_a_tabela_e_a_coluna():
    plano = [s for s, _ in _module()["plan"](FakeDB())]
    assert plano == [_module()["CREATE_SQL"], _module()["ADD_COLUMN_SQL"]]
    assert _module()["ADD_COLUMN_SQL"].endswith("NULL DEFAULT NULL, ALGORITHM=INSTANT")


def test_convergido_nao_faz_nada():
    assert _module()["plan"](FakeDB(**CONVERGIDO)) == []
    assert _module()["plan"](FakeDB(**{**CONVERGIDO, "coluna": ("enum('period', 'manual')", "YES")})) == []


@pytest.mark.parametrize(
    "ajuste",
    [
        {"coluna": ("varchar(16)", "YES")},
        {"coluna": ("enum('period','manual')", "NO")},
        {"coluna": ("enum('manual','period')", "YES")},
        {"colunas": {**_module()["EXPECTED_COLUMNS"], "ends_on": ("datetime", "NO")}},
        {"colunas": {k: v for k, v in _module()["EXPECTED_COLUMNS"].items() if k != "cancelled_at"}},
        {"indice": ("user_id",)},
    ],
)
def test_forma_divergente_falha_antes_do_primeiro_statement(ajuste):
    with pytest.raises(_module()["DeloadError"], match="Nenhum statement executado"):
        _module()["up"](FakeDB(**{**CONVERGIDO, **ajuste}))


def test_tabelas_base_ausentes_e_dry_run():
    with pytest.raises(_module()["DeloadError"]):
        _module()["plan"](FakeDB(users=False))
    with pytest.raises(_module()["DeloadError"]):
        _module()["plan"](FakeDB(activities=False))
    assert len(_module()["plan"](FakeDB(users=False, activities=False, dry_run=True))) == 2


def test_down():
    assert [s for s, _ in _module()["plan_down"](FakeDB(**CONVERGIDO))] == [
        _module()["DROP_COLUMN_SQL"],
        _module()["DROP_TABLE_SQL"],
    ]
    assert _module()["plan_down"](FakeDB()) == []


def test_ddl_fk_so_para_users_e_sem_literal_com_dois_pontos():
    sql = _sql(_module()["CREATE_SQL"])
    assert re.findall(r"REFERENCES (\w+)", sql) == ["users"]
    assert "ON DELETE CASCADE" in sql and "exercise_catalog" not in sql
    assert "KEY idx_dp_user_period (user_id, starts_on, ends_on)" in sql
    for statement in (_module()["CREATE_SQL"], _module()["ADD_COLUMN_SQL"]):
        assert ":" not in statement


def test_expected_columns_e_o_create_descrevem_as_mesmas_colunas():
    linhas = [linha.strip() for linha in _module()["CREATE_SQL"].splitlines()]
    nomes = [
        linha.split()[0]
        for linha in linhas
        if re.match(r"^[a-z_]+ ", linha) and not linha.startswith(("CREATE", "UNIQUE", "KEY", "CONSTRAINT"))
    ]
    assert set(nomes) == set(_module()["EXPECTED_COLUMNS"])
