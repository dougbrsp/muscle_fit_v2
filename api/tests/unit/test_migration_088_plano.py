"""Plano da migração 088 (`next_set_load_rule_sets`, regras de carga R2).

- Aurora (recorte real somente leitura, sem a tabela) -> 1 `CREATE TABLE`;
  DRY-RUN igual (não há dependência).
- Convergido -> 0 (inclusive com a expressão reescrita pelo MySQL); forma
  divergente -> erro antes do primeiro statement; `plan_down` inverso.
- DDL: UNIQUE da versão, do vigente e do rascunho; nenhuma FK; nenhum ':' em
  literal; colunas no tamanho que o dono e o loader gravam.

Rodar: cd api && python -m pytest tests/unit/test_migration_088_plano.py
"""

from __future__ import annotations

import functools
import json
import re
from pathlib import Path

import pytest

from app import migrate as m
from app import next_set_load_rules as loader
from app.domain import next_set_load as nsl
from tests.support.safety_032_033 import SafetySnapshotDB

MIGRATION = m.DEFAULT_MIGRATIONS_DIR / "088_next_set_load_rule_sets.py"
SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "unit" / "data" / "aurora_schema_2026_09_17_035.json"
T = "next_set_load_rule_sets"

# Como o MySQL 8 devolve as expressões em `GENERATION_EXPRESSION`.
MYSQL_PUBLISHED = "if(((`status` = _utf8mb4\\'published\\') and (`superseded_at` is null)),1,NULL)"
MYSQL_DRAFT = "if((`status` = _utf8mb4\\'draft\\'),1,NULL)"


@functools.lru_cache(maxsize=1)
def _mod() -> dict:
    return m.load_python_migration(MIGRATION).__globals__


def _sql(texto: str) -> str:
    return " ".join(texto.split())


def _indices_esperados() -> dict:
    return {nome: (0 if unico else 1, cols) for nome, (unico, cols) in _mod()["EXPECTED_INDEXES"].items()}


def _geradas_mysql() -> dict:
    return {"current_published": MYSQL_PUBLISHED, "current_draft": MYSQL_DRAFT}


class FakeDB:
    def __init__(self, *, tabela=True, colunas=None, indices=None, geradas=None, dry_run=False, engine="InnoDB"):
        self.tabela = tabela
        self.colunas = dict(_mod()["EXPECTED_COLUMNS"] if colunas is None else colunas)
        self.indices = _indices_esperados() if indices is None else indices
        self.geradas = _geradas_mysql() if geradas is None else geradas
        self.dry_run = dry_run
        self.engine = engine

    def query(self, sql, params=None):
        assert "information_schema" in sql
        if "information_schema.TABLES" in sql:
            return [{"TABLE_NAME": T, "ENGINE": self.engine}] if self.tabela else []
        if "information_schema.COLUMNS" in sql:
            if not self.tabela:
                return []
            return [
                {"TABLE_NAME": T, "COLUMN_NAME": n, "COLUMN_TYPE": t, "IS_NULLABLE": nu,
                 "GENERATION_EXPRESSION": self.geradas.get(n, "")}
                for n, (t, nu) in self.colunas.items()
            ]
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
    assert [(_sql(s), p) for s, p in _mod()["plan"](db)] == [(_sql(_mod()["CREATE_SQL"]), None)]
    assert all("information_schema" in q for q in db.queries)


def test_dry_run_com_a_tabela_ausente_planeja_o_create():
    assert [s for s, _ in _mod()["plan"](FakeDB(tabela=False, dry_run=True))] == [_mod()["CREATE_SQL"]]


def test_convergido_nao_faz_nada_com_a_expressao_e_a_largura_do_mysql():
    assert _mod()["plan"](FakeDB()) == []
    largura = {**_mod()["EXPECTED_COLUMNS"], "version_number": ("int(11)", "YES"), "reps_floor": ("tinyint(4)", "NO")}
    assert _mod()["plan"](FakeDB(colunas=largura)) == []
    # como nós escrevemos também converge
    assert _mod()["plan"](FakeDB(geradas=dict(_mod()["GENERATED_COLUMNS"]))) == []


@pytest.mark.parametrize(
    "ajuste",
    [
        {"colunas": {**_mod()["EXPECTED_COLUMNS"], "rules_json": ("json", "YES")}},
        {"colunas": {**_mod()["EXPECTED_COLUMNS"], "reps_floor": ("smallint", "NO")}},
        {"colunas": {**_mod()["EXPECTED_COLUMNS"], "status": ("varchar(32)", "NO")}},
        {"colunas": {k: v for k, v in _mod()["EXPECTED_COLUMNS"].items() if k != "superseded_at"}},
        {"geradas": {**_geradas_mysql(), "current_draft": ""}},
        {"geradas": {**_geradas_mysql(), "current_published": "if((`status` = _utf8mb4\\'published\\'),1,NULL)"}},
        {"indices": {}},
        {"indices": {**_indices_esperados(), "uq_nslrs_current_draft": (1, ("current_draft",))}},
        {"indices": {**_indices_esperados(), "uq_nslrs_version": (0, ("id",))}},
        {"engine": "MyISAM"},
    ],
)
def test_forma_divergente_falha_antes_do_primeiro_statement(ajuste):
    with pytest.raises(_mod()["NextSetLoadRuleSetsError"], match="Nenhum statement executado"):
        _mod()["up"](FakeDB(**ajuste))


def test_down():
    assert [s for s, _ in _mod()["plan_down"](FakeDB())] == [_mod()["DROP_SQL"]]
    assert _mod()["plan_down"](FakeDB(tabela=False)) == []


def test_ddl_unicos_sem_fk_e_sem_dois_pontos():
    sql = _sql(_mod()["CREATE_SQL"])
    assert "UNIQUE KEY uq_nslrs_version (version_number)" in sql
    assert "UNIQUE KEY uq_nslrs_current_published (current_published)" in sql
    assert "UNIQUE KEY uq_nslrs_current_draft (current_draft)" in sql
    assert "GENERATED ALWAYS AS (IF(status = 'published' AND superseded_at IS NULL, 1, NULL)) VIRTUAL" in sql
    assert "GENERATED ALWAYS AS (IF(status = 'draft', 1, NULL)) VIRTUAL" in sql
    assert "FOREIGN KEY" not in sql and "REFERENCES" not in sql
    assert ":" not in sql  # literal com ':' viraria parâmetro no shim da Data API
    assert "status VARCHAR(16) NOT NULL DEFAULT 'draft'" in sql


def test_expected_columns_e_o_create_descrevem_as_mesmas_colunas():
    linhas = [linha.strip() for linha in _mod()["CREATE_SQL"].splitlines()]
    nomes = [linha.split()[0] for linha in linhas
             if re.match(r"^[a-z_]+ ", linha) and not linha.startswith(("CREATE", "UNIQUE", "KEY", "CONSTRAINT"))]
    assert set(nomes) == set(_mod()["EXPECTED_COLUMNS"])


def test_colunas_cabem_o_que_o_dono_e_o_loader_gravam():
    colunas = _mod()["EXPECTED_COLUMNS"]
    assert _mod()["TABLE"] == loader.TABLE
    assert len(nsl.RULESET_ENGINE_VERSION) <= 32
    assert max(len(s) for s in (loader.STATUS_DRAFT, loader.STATUS_PUBLISHED)) <= 16
    assert colunas["notes"][0] == f"varchar({loader.NOTES_MAX_LENGTH})"
    # TINYINT (assinado) cobre o teto de reps do dono.
    assert nsl.REPS_CEILING_MAX <= 127
