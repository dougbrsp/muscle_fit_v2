"""Plano da migração 080 (`food_items` + `food_portions`, nutrição L3a).

- Aurora (recorte real somente leitura `aurora_schema_2026_09_17_035.json`, sem
  as tabelas) -> 2 `CREATE TABLE`; o plano só lê `information_schema`.
- Convergido (sintético) -> 0; só uma tabela faltando -> 1.
- Forma divergente (tipo, nulabilidade, índice ausente, FULLTEXT trocado por
  BTREE, prefixo do índice, UNIQUE, ENGINE) -> erro ANTES do primeiro statement.
- `users` ausente: erro numa execução real, os 2 CREATE em DRY-RUN.
- DDL: FULLTEXT no CREATE (antes do seed da 081, R1), UNIQUE (source,
  source_id), FK só para `users`, colunas com os códigos da RDC 429 do motor.

Rodar: cd api && python -m pytest tests/unit/test_migration_080_plano.py
"""

from __future__ import annotations

import functools
import json
import re
from pathlib import Path

import pytest

from app import migrate as m
from app.domain.nutrition import catalog
from tests.support.safety_032_033 import SafetySnapshotDB

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "080_food_catalog.py"
SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "unit" / "data" / "aurora_schema_2026_09_17_035.json"
ITEMS, PORTIONS = "food_items", "food_portions"


@functools.lru_cache(maxsize=1)
def _module() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


def _sql(texto: str) -> str:
    return " ".join(texto.split())


def _indices_convergidos() -> dict:
    mod = _module()
    indices = {}
    for tabela, esperados in ((ITEMS, mod["EXPECTED_ITEM_INDEXES"]), (PORTIONS, mod["EXPECTED_PORTION_INDEXES"])):
        for nome, (unico, cols, tipo, prefixos) in esperados.items():
            indices[(tabela, nome)] = (0 if unico else 1, cols, tipo, prefixos)
    return indices


class FakeDB:
    def __init__(self, *, tabelas=(ITEMS, PORTIONS), users=True, colunas=None, indices=None, engine="InnoDB",
                 dry_run=False):
        mod = _module()
        self.tabelas = set(tabelas)
        self.users = users
        self.colunas = colunas or {ITEMS: dict(mod["EXPECTED_ITEM_COLUMNS"]), PORTIONS: dict(mod["EXPECTED_PORTION_COLUMNS"])}
        self.indices = _indices_convergidos() if indices is None else indices
        self.engine = engine
        self.dry_run = dry_run
        self.queries: list[str] = []

    def query(self, sql, params=None):
        self.queries.append(sql)
        if "information_schema.TABLES" in sql:
            linhas = [{"TABLE_NAME": t, "ENGINE": self.engine} for t in sorted(self.tabelas)]
            if self.users:
                linhas.append({"TABLE_NAME": "users", "ENGINE": "InnoDB"})
            return linhas
        if "information_schema.COLUMNS" in sql:
            linhas = []
            if self.users:
                linhas.append({"TABLE_NAME": "users", "COLUMN_NAME": "id", "COLUMN_TYPE": "bigint(20)", "IS_NULLABLE": "NO",
                               "COLUMN_KEY": "PRI"})
            for tabela in sorted(self.tabelas):
                for nome, (tipo, nulo) in self.colunas[tabela].items():
                    linhas.append({"TABLE_NAME": tabela, "COLUMN_NAME": nome, "COLUMN_TYPE": tipo, "IS_NULLABLE": nulo,
                                   "COLUMN_KEY": ""})
            return linhas
        if "information_schema.STATISTICS" in sql:
            return [
                {"TABLE_NAME": tabela, "INDEX_NAME": nome, "SEQ_IN_INDEX": seq, "COLUMN_NAME": col, "NON_UNIQUE": nu,
                 "INDEX_TYPE": tipo, "SUB_PART": prefixos[seq - 1]}
                for (tabela, nome), (nu, cols, tipo, prefixos) in self.indices.items()
                if tabela in self.tabelas
                for seq, col in enumerate(cols, 1)
            ]
        raise AssertionError(f"leitura inesperada: {sql}")

    def execute(self, sql, params=None):
        raise AssertionError(f"escrita no plano: {sql}")


def test_aurora_hoje_cria_as_duas_tabelas():
    snap = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    nomes = {t["table_name"] for t in snap["tables"]}
    assert ITEMS not in nomes and PORTIONS not in nomes
    db = SafetySnapshotDB(snap)
    statements = _module()["plan"](db)
    assert [_sql(s) for s, _ in statements] == [_sql(_module()["CREATE_ITEMS_SQL"]), _sql(_module()["CREATE_PORTIONS_SQL"])]
    assert all(q.strip().upper().startswith("SELECT") and "information_schema" in q for q in db.queries)


def test_convergido_nao_faz_nada_e_parcial_cria_so_o_que_falta():
    assert _module()["plan"](FakeDB()) == []
    so_porcoes = _module()["plan"](FakeDB(tabelas=(ITEMS,)))
    assert [_sql(s) for s, _ in so_porcoes] == [_sql(_module()["CREATE_PORTIONS_SQL"])]


def _sem(dicionario, chave):
    return {k: v for k, v in dicionario.items() if k != chave}


def _com(dicionario, chave, valor):
    return {**dicionario, chave: valor}


def _colunas(**ajuste):
    mod = _module()
    base = {ITEMS: dict(mod["EXPECTED_ITEM_COLUMNS"]), PORTIONS: dict(mod["EXPECTED_PORTION_COLUMNS"])}
    base.update(ajuste)
    return base


@pytest.mark.parametrize(
    "ajuste",
    [
        {"colunas": _colunas(food_items=_com(_module()["EXPECTED_ITEM_COLUMNS"], "name", ("varchar(120)", "NO")))},
        {"colunas": _colunas(food_items=_com(_module()["EXPECTED_ITEM_COLUMNS"], "energy_kcal", ("decimal(7,2)", "NO")))},
        {"colunas": _colunas(food_items=_sem(_module()["EXPECTED_ITEM_COLUMNS"], "name_normalized"))},
        {"colunas": _colunas(food_portions=_com(_module()["EXPECTED_PORTION_COLUMNS"], "amount", ("int", "NO")))},
        {"indices": _sem(_indices_convergidos(), (ITEMS, "ft_fi_name"))},
        {"indices": _com(_indices_convergidos(), (ITEMS, "ft_fi_name"), (1, ("name_normalized",), "BTREE", (None,)))},
        {"indices": _com(_indices_convergidos(), (ITEMS, "idx_fi_name_prefix"), (1, ("name_normalized",), "BTREE", (None,)))},
        {"indices": _com(_indices_convergidos(), (ITEMS, "uq_fi_source"), (1, ("source", "source_id"), "BTREE", (None, None)))},
        {"indices": _sem(_indices_convergidos(), (PORTIONS, "idx_fp_food"))},
        {"engine": "MyISAM"},
    ],
)
def test_forma_divergente_falha_antes_do_primeiro_statement(ajuste):
    with pytest.raises(_module()["FoodCatalogError"], match="Nenhum statement executado"):
        _module()["up"](FakeDB(**ajuste))


def test_users_ausente_falha_e_dry_run_planeja_os_dois_creates():
    with pytest.raises(_module()["FoodCatalogError"]):
        _module()["plan"](FakeDB(tabelas=(), users=False))
    assert len(_module()["plan"](FakeDB(tabelas=(), users=False, dry_run=True))) == 2


def test_plan_down_apaga_porcoes_antes_dos_itens():
    assert [s for s, _ in _module()["plan_down"](FakeDB())] == [_module()["DROP_PORTIONS_SQL"], _module()["DROP_ITEMS_SQL"]]
    assert _module()["plan_down"](FakeDB(tabelas=())) == []


def test_ddl_fulltext_no_create_unique_da_fonte_e_fk_so_para_users():
    itens = _sql(_module()["CREATE_ITEMS_SQL"])
    porcoes = _sql(_module()["CREATE_PORTIONS_SQL"])
    assert "FULLTEXT KEY ft_fi_name (name_normalized)" in itens
    assert "UNIQUE KEY uq_fi_source (source, source_id)" in itens
    assert "KEY idx_fi_name_prefix (name_normalized(64))" in itens
    assert "KEY idx_fi_barcode (barcode)" in itens
    for sql in (itens, porcoes):
        assert re.findall(r"REFERENCES (\w+)", sql) == ["users"]
        assert "ON DELETE CASCADE" in sql and "exercise_catalog" not in sql
        assert "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4" in sql
        assert ":" not in sql.split("COLLATE")[0], "literal com ':' viraria parâmetro no shim da Data API"


@pytest.mark.parametrize(("sql", "chave"), [("CREATE_ITEMS_SQL", "EXPECTED_ITEM_COLUMNS"), ("CREATE_PORTIONS_SQL", "EXPECTED_PORTION_COLUMNS")])
def test_expected_columns_e_o_create_descrevem_as_mesmas_colunas(sql, chave):
    linhas = [linha.strip() for linha in _module()[sql].splitlines()]
    nomes = [linha.split()[0] for linha in linhas
             if re.match(r"^[a-z_0-9]+ ", linha) and not linha.startswith(("CREATE", "UNIQUE", "KEY", "CONSTRAINT", "FULLTEXT"))]
    assert set(nomes) == set(_module()[chave])


def test_colunas_por_100_sao_os_codigos_do_motor():
    colunas = _module()["EXPECTED_ITEM_COLUMNS"]
    assert set(catalog.PER_100_FIELDS) <= set(colunas)
    assert set(catalog.SERVING_FIELDS) <= set(colunas)
    assert all(colunas[c][1] == "YES" for c in catalog.PER_100_FIELDS)  # NULL = a fonte não informa
