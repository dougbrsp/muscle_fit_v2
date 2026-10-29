"""Plano da migração 079 (`nutrition_daily_targets`, nutrição L2b).

- Aurora (recorte real somente leitura, sem a tabela) -> 1 `CREATE TABLE`.
- Convergido -> 0; forma divergente -> erro antes do primeiro statement.
- DDL: uma meta por (aluno, dia), `plan_id` lógico com índice, FK só para `users`.

Rodar: cd api && python -m pytest tests/unit/test_migration_079_plano.py
"""

from __future__ import annotations

import functools
import json
import re
from pathlib import Path

import pytest

from app import migrate as m
from tests.support.safety_032_033 import SafetySnapshotDB

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "079_nutrition_daily_targets.py"
SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "unit" / "data" / "aurora_schema_2026_09_17_035.json"
T = "nutrition_daily_targets"


@functools.lru_cache(maxsize=1)
def _module() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


def _sql(texto: str) -> str:
    return " ".join(texto.split())


class FakeDB:
    def __init__(self, *, tabela=True, users=True, colunas=None, indices=None, dry_run=False):
        self.tabela = tabela
        self.users = users
        self.colunas = dict(_module()["EXPECTED_COLUMNS"] if colunas is None else colunas)
        self.indices = (
            {"uq_ndt_user_date": (0, ("user_id", "local_date")), "idx_ndt_plan": (1, ("plan_id",))}
            if indices is None else indices
        )
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
    db = SafetySnapshotDB(snap)
    assert [(_sql(s), p) for s, p in _module()["plan"](db)] == [(_sql(_module()["CREATE_SQL"]), None)]
    assert all("information_schema" in q for q in db.queries)


def test_convergido_nao_faz_nada():
    assert _module()["plan"](FakeDB()) == []


@pytest.mark.parametrize(
    "ajuste",
    [
        {"colunas": {**_module()["EXPECTED_COLUMNS"], "kcal_target": ("decimal(8,2)", "NO")}},
        {"colunas": {**_module()["EXPECTED_COLUMNS"], "inputs_json": ("text", "YES")}},
        {"colunas": {k: v for k, v in _module()["EXPECTED_COLUMNS"].items() if k != "plan_id"}},
        {"indices": {"idx_ndt_plan": (1, ("plan_id",))}},
        {"indices": {"uq_ndt_user_date": (0, ("local_date", "user_id")), "idx_ndt_plan": (1, ("plan_id",))}},
    ],
)
def test_forma_divergente_falha_antes_do_primeiro_statement(ajuste):
    with pytest.raises(_module()["NutritionDailyTargetsError"], match="Nenhum statement executado"):
        _module()["up"](FakeDB(**ajuste))


def test_users_ausente_e_down():
    with pytest.raises(_module()["NutritionDailyTargetsError"]):
        _module()["plan"](FakeDB(tabela=False, users=False))
    assert len(_module()["plan"](FakeDB(tabela=False, users=False, dry_run=True))) == 1
    assert [s for s, _ in _module()["plan_down"](FakeDB())] == [_module()["DROP_SQL"]]
    assert _module()["plan_down"](FakeDB(tabela=False)) == []


def test_ddl_uma_meta_por_dia_e_referencia_logica_ao_plano():
    sql = _sql(_module()["CREATE_SQL"])
    assert "UNIQUE KEY uq_ndt_user_date (user_id, local_date)" in sql
    assert "KEY idx_ndt_plan (plan_id)" in sql
    assert re.findall(r"REFERENCES (\w+)", sql) == ["users"]
    assert "exercise_catalog" not in sql and "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4" in sql
    # NULL = não calculável (nunca 0): as metas não têm DEFAULT.
    for coluna in ("kcal_target", "protein_g", "carbs_g", "fat_g", "fiber_g"):
        assert re.search(rf"{coluna} DECIMAL\(\d,2\) NULL,", sql), coluna
    # A decisão do motor sobre o treino é GRAVADA (nunca recalculada pela data na leitura).
    assert "training_known TINYINT(1) NOT NULL DEFAULT 1," in sql
    # Hash das entradas: o recálculo pula o upsert quando nada mudou.
    assert "inputs_hash CHAR(64) NULL," in sql


def test_expected_columns_e_o_create_descrevem_as_mesmas_colunas():
    linhas = [linha.strip() for linha in _module()["CREATE_SQL"].splitlines()]
    nomes = [linha.split()[0] for linha in linhas if re.match(r"^[a-z_]+ ", linha) and not linha.startswith(("CREATE", "UNIQUE", "KEY", "CONSTRAINT"))]
    assert set(nomes) == set(_module()["EXPECTED_COLUMNS"])
