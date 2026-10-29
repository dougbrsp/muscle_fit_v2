"""Plano da migração 085 (`ai_usage_log`, nutrição L5a).

- Aurora (recorte real somente leitura, sem a tabela) -> 1 `CREATE TABLE`.
- Convergido -> 0; forma divergente -> erro antes do primeiro statement.
- DDL: índices da cota e do sweep, FK só para `users`, colunas no tamanho que o
  serviço e o motor usam.

Rodar: cd api && python -m pytest tests/unit/test_migration_085_plano.py
"""

from __future__ import annotations

import functools
import json
import re
from pathlib import Path

import pytest

from app import migrate as m
from app import nutrition_ai_quota as quota
from app.domain.nutrition import ai_usage
from app.nutrition_schema import AI_USAGE_TABLE
from tests.support.safety_032_033 import SafetySnapshotDB

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "085_ai_usage_log.py"
SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "unit" / "data" / "aurora_schema_2026_09_17_035.json"
T = "ai_usage_log"


@functools.lru_cache(maxsize=1)
def _module() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


def _sql(texto: str) -> str:
    return " ".join(texto.split())


def _indices_esperados() -> dict:
    return {nome: (0 if unico else 1, cols) for nome, (unico, cols) in _module()["EXPECTED_INDEXES"].items()}


class FakeDB:
    def __init__(self, *, tabela=True, users=True, colunas=None, indices=None, dry_run=False, engine="InnoDB"):
        self.tabela = tabela
        self.users = users
        self.colunas = dict(_module()["EXPECTED_COLUMNS"] if colunas is None else colunas)
        self.indices = _indices_esperados() if indices is None else indices
        self.dry_run = dry_run
        self.engine = engine

    def query(self, sql, params=None):
        if "information_schema.TABLES" in sql:
            return ([{"TABLE_NAME": T, "ENGINE": self.engine}] if self.tabela else []) + (
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


def test_convergido_nao_faz_nada_mesmo_com_largura_dos_inteiros():
    assert _module()["plan"](FakeDB()) == []
    largura = {**_module()["EXPECTED_COLUMNS"], "tokens_in": ("int(11)", "YES"), "image_count": ("smallint(6)", "NO")}
    assert _module()["plan"](FakeDB(colunas=largura)) == []


@pytest.mark.parametrize(
    "ajuste",
    [
        {"colunas": {**_module()["EXPECTED_COLUMNS"], "cost_usd": ("decimal(8,4)", "YES")}},
        {"colunas": {**_module()["EXPECTED_COLUMNS"], "status": ("varchar(16)", "YES")}},
        {"colunas": {k: v for k, v in _module()["EXPECTED_COLUMNS"].items() if k != "error_code"}},
        {"indices": {}},
        {"indices": {**_indices_esperados(), "idx_aul_user_feature_started": (1, ("user_id", "started_at"))}},
        {"indices": {**_indices_esperados(), "idx_aul_status_started": (0, ("status", "started_at"))}},
        {"engine": "MyISAM"},
    ],
)
def test_forma_divergente_falha_antes_do_primeiro_statement(ajuste):
    with pytest.raises(_module()["AiUsageLogError"], match="Nenhum statement executado"):
        _module()["up"](FakeDB(**ajuste))


def test_users_ausente_e_down():
    with pytest.raises(_module()["AiUsageLogError"]):
        _module()["plan"](FakeDB(tabela=False, users=False))
    assert len(_module()["plan"](FakeDB(tabela=False, users=False, dry_run=True))) == 1
    assert [s for s, _ in _module()["plan_down"](FakeDB())] == [_module()["DROP_SQL"]]
    assert _module()["plan_down"](FakeDB(tabela=False)) == []


def test_ddl_indices_da_cota_e_do_sweep_e_fk_so_para_users():
    sql = _sql(_module()["CREATE_SQL"])
    assert "KEY idx_aul_user_feature_started (user_id, feature, started_at)" in sql
    assert "KEY idx_aul_status_started (status, started_at)" in sql
    assert re.findall(r"REFERENCES (\w+)", sql) == ["users"]
    assert "ON DELETE CASCADE" in sql and "exercise_catalog" not in sql
    assert "UNIQUE" not in sql  # a cota é COUNT por (usuário, feature, dia), sem unicidade
    assert "started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP" in sql  # relógio do banco, o da cota
    assert ":" not in sql  # literal com ':' viraria parâmetro no shim da Data API


def test_expected_columns_e_o_create_descrevem_as_mesmas_colunas():
    linhas = [linha.strip() for linha in _module()["CREATE_SQL"].splitlines()]
    nomes = [linha.split()[0] for linha in linhas
             if re.match(r"^[a-z_]+ ", linha) and not linha.startswith(("CREATE", "UNIQUE", "KEY", "CONSTRAINT"))]
    assert set(nomes) == set(_module()["EXPECTED_COLUMNS"])


def test_colunas_cabem_o_que_o_motor_e_o_servico_gravam():
    colunas = _module()["EXPECTED_COLUMNS"]
    assert _module()["TABLE"] == AI_USAGE_TABLE
    # Custo: DECIMAL(10,6) = as casas do motor.
    assert colunas["cost_usd"][0] == f"decimal(10,{ai_usage.COST_USD_DECIMAL_PLACES})"
    # Texto cortado pelo serviço no tamanho da coluna.
    for coluna, limite in quota._TEXT_COLUMN_MAX.items():
        assert colunas[coluna][0] == f"varchar({limite})", coluna
    # Códigos do motor cabem nas colunas de código.
    assert max(len(f.value) for f in ai_usage.AiFeature) <= 32
    assert max(len(s.value) for s in ai_usage.AiUsageStatus) <= 16
