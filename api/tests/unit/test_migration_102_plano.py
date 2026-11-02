"""Plano da migração 102 (`user_favorite_foods`, nutrição N3).

- Aurora (recorte real somente leitura, sem a tabela) -> 1 `CREATE TABLE`.
- Convergido -> 0; forma divergente -> erro antes do primeiro statement.
- DDL: único por (usuário, alimento), FK só para `users`, reversível.

Rodar: cd api && python -m pytest tests/unit/test_migration_102_plano.py
"""

from __future__ import annotations

import functools
import json
import re
from pathlib import Path

import pytest

from app import migrate as m
from tests.support.safety_032_033 import SafetySnapshotDB

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "102_user_favorite_foods.py"
SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "unit" / "data" / "aurora_schema_2026_09_17_035.json"
T = "user_favorite_foods"


@functools.lru_cache(maxsize=1)
def _module() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


def _sql(texto: str) -> str:
    return " ".join(texto.split())


def _indices_ok() -> dict:
    return {nome: (0 if unico else 1, cols) for nome, (unico, cols) in _module()["EXPECTED_INDEXES"].items()}


class FakeDB:
    def __init__(self, *, tabela=True, users=True, colunas=None, indices=None, dry_run=False):
        self.tabela = tabela
        self.users = users
        self.colunas = dict(_module()["EXPECTED_COLUMNS"] if colunas is None else colunas)
        self.indices = _indices_ok() if indices is None else indices
        self.dry_run = dry_run

    def query(self, sql, params=None):
        if "information_schema.TABLES" in sql:
            return ([{"TABLE_NAME": T, "ENGINE": "InnoDB"}] if self.tabela else []) + (
                [{"TABLE_NAME": "users", "ENGINE": "InnoDB"}] if self.users else []
            )
        if "information_schema.COLUMNS" in sql:
            linhas = [{"TABLE_NAME": "users", "COLUMN_NAME": "id", "COLUMN_TYPE": "bigint", "IS_NULLABLE": "NO",
                       "COLUMN_KEY": "PRI"}] if self.users else []
            if self.tabela:
                linhas += [{"TABLE_NAME": T, "COLUMN_NAME": n, "COLUMN_TYPE": t, "IS_NULLABLE": nu, "COLUMN_KEY": ""}
                           for n, (t, nu) in self.colunas.items()]
            return linhas
        if "information_schema.STATISTICS" in sql:
            return [
                {"TABLE_NAME": T, "INDEX_NAME": nome, "SEQ_IN_INDEX": seq, "COLUMN_NAME": col, "NON_UNIQUE": nu}
                for nome, (nu, cols) in (self.indices.items() if self.tabela else [])
                for seq, col in enumerate(cols, 1)
            ]
        raise AssertionError(f"leitura inesperada: {sql}")

    def execute(self, sql, params=None):
        raise AssertionError(f"escrita no plano: {sql}")


def test_aurora_hoje_cria_a_tabela_com_um_statement():
    snap = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert T not in {t["table_name"] for t in snap["tables"]}
    db = SafetySnapshotDB(snap)
    assert [(_sql(s), p) for s, p in _module()["plan"](db)] == [(_sql(_module()["CREATE_SQL"]), None)]
    assert all("information_schema" in q for q in db.queries)


def test_convergido_nao_faz_nada_mesmo_com_largura_do_int():
    assert _module()["plan"](FakeDB()) == []
    assert _module()["plan"](FakeDB(colunas={**_module()["EXPECTED_COLUMNS"], "position": ("int(11)", "NO")})) == []


@pytest.mark.parametrize(
    "ajuste",
    [
        {"colunas": {**_module()["EXPECTED_COLUMNS"], "default_quantity": ("decimal(8,2)", "YES")}},
        {"colunas": {**_module()["EXPECTED_COLUMNS"], "food_id": ("bigint", "YES")}},
        {"colunas": {k: v for k, v in _module()["EXPECTED_COLUMNS"].items() if k != "position"}},
        {"indices": {}},
        {"indices": {**_indices_ok(), "uq_ufav_user_food": (1, ("user_id", "food_id"))}},
        {"indices": {**_indices_ok(), "uq_ufav_user_food": (0, ("food_id", "user_id"))}},
        {"indices": {**_indices_ok(), "idx_ufav_user_pos": (0, ("user_id", "position", "id"))}},
    ],
)
def test_forma_divergente_falha_antes_do_primeiro_statement(ajuste):
    with pytest.raises(_module()["UserFavoriteFoodsError"], match="Nenhum statement executado"):
        _module()["up"](FakeDB(**ajuste))


def test_users_ausente_e_down_reversivel():
    with pytest.raises(_module()["UserFavoriteFoodsError"]):
        _module()["plan"](FakeDB(tabela=False, users=False))
    assert len(_module()["plan"](FakeDB(tabela=False, users=False, dry_run=True))) == 1
    assert [s for s, _ in _module()["plan_down"](FakeDB())] == [_module()["DROP_SQL"]]
    assert _module()["plan_down"](FakeDB(tabela=False)) == []


def test_ddl_unico_por_usuario_e_alimento_fk_so_para_users():
    sql = _sql(_module()["CREATE_SQL"])
    assert "CREATE TABLE IF NOT EXISTS user_favorite_foods" in sql
    assert "UNIQUE KEY uq_ufav_user_food (user_id, food_id)" in sql
    assert re.findall(r"REFERENCES (\w+)", sql) == ["users"]
    assert "ON DELETE CASCADE" in sql and "exercise_catalog" not in sql
    assert "default_quantity DECIMAL(10,3) NULL," in sql  # o mesmo tipo de meal_items.quantity


def test_expected_columns_e_o_create_descrevem_as_mesmas_colunas():
    linhas = [linha.strip() for linha in _module()["CREATE_SQL"].splitlines()]
    nomes = [linha.split()[0] for linha in linhas
             if re.match(r"^[a-z_]+ ", linha) and not linha.startswith(("CREATE", "UNIQUE", "KEY", "CONSTRAINT"))]
    assert set(nomes) == set(_module()["EXPECTED_COLUMNS"])
