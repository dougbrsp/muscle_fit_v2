"""Plano da migracao 026 contra o schema REAL da Aurora de producao
(`information_schema` extraido em 2026-09-14, recorte em
tests/unit/data/aurora_schema_2026_09_14_026.json): 0 statements.

O dublê responde as mesmas leituras que a 026 faz (`TABLES`, `COLUMNS`,
`STATISTICS`, `KEY_COLUMN_USAGE`+`REFERENTIAL_CONSTRAINTS`) a partir do
snapshot; qualquer contagem de orfaos ou escrita seria sinal de que a 026 iria
alterar a Aurora. A mesma logica contra MySQL real, nos dois backends, esta em
tests/integration/dataapi/test_migration_026_sessoes_fichas.py.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app import migrate as m
from tests.support import sessoes_fichas_schema as ssf

SNAPSHOT = json.loads((Path(__file__).parent / "data" / "aurora_schema_2026_09_14_026.json").read_text(encoding="utf-8"))


class SnapshotDB:
    def __init__(self, snapshot: dict):
        self.snapshot = snapshot
        self.queries: list[str] = []
        self.executed: list[str] = []

    def query(self, sql, params=None):
        self.queries.append(sql)
        wanted = set(params or ())
        s = self.snapshot
        if "information_schema.TABLES" in sql:
            return [{"TABLE_NAME": t["table_name"], "ENGINE": t["engine"]} for t in s["tables"] if t["table_name"] in wanted]
        if "information_schema.COLUMNS" in sql:
            return [
                {"TABLE_NAME": c["t"], "COLUMN_NAME": c["col"], "COLUMN_TYPE": c["type"], "IS_NULLABLE": c["nullable"], "COLUMN_DEFAULT": c["dflt"]}
                for c in sorted(s["columns"], key=lambda c: (c["t"], c["pos"]))
                if c["t"] in wanted
            ]
        if "information_schema.STATISTICS" in sql:
            return [
                {"TABLE_NAME": i["t"], "INDEX_NAME": i["idx"], "COLUMN_NAME": i["col"]}
                for i in sorted(s["indexes"], key=lambda i: (i["t"], i["idx"], i["seq"]))
                if i["t"] in wanted
            ]
        if "KEY_COLUMN_USAGE" in sql:
            return [
                {
                    "TABLE_NAME": f["t"],
                    "CONSTRAINT_NAME": f["fk"],
                    "COLUMN_NAME": f["col"],
                    "REFERENCED_TABLE_NAME": f["ref_t"],
                    "REFERENCED_COLUMN_NAME": f["ref_col"],
                    "DELETE_RULE": f["on_delete"],
                }
                for f in s["foreign_keys"]
                if f["t"] in wanted
            ]
        raise AssertionError(f"leitura inesperada contra a Aurora: {sql}")

    def query_one(self, sql, params=None):
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql, params=None):
        self.executed.append(sql)


def _plan(snapshot: dict) -> tuple[list[str], SnapshotDB]:
    db = SnapshotDB(snapshot)
    plan = m.load_python_migration(ssf.MIGRATION_PATH).__globals__["plan"]
    return [" ".join(sql.split()) for sql, _ in plan(db)], db


def test_snapshot_e_da_aurora_3_mysql_8():
    assert SNAPSHOT["versao"][0]["mysql"].startswith("8.0")


def test_aurora_de_producao_026_executa_zero_statements():
    statements, db = _plan(SNAPSHOT)
    assert statements == []
    assert len(db.queries) == 4  # so as quatro leituras de information_schema, sem contagem de orfaos

    facade = m.MigrationDB(conn=None)  # up() pela fachada do runner, sem conexao
    facade.query, facade.query_one = db.query, db.query_one
    facade.execute = lambda sql, params=None: pytest.fail(f"escrita na Aurora: {sql}")
    m.load_python_migration(ssf.MIGRATION_PATH)(facade)
    assert facade.statements == []


def test_mesmo_dublê_sem_as_colunas_dos_guards_planeja_o_baseline():
    """Controle: o dublê nao e trivialmente vazio - sem as duas colunas que so
    os guards criavam, o plano e o mesmo validado no MySQL real."""
    snapshot = copy.deepcopy(SNAPSHOT)
    removed = set(ssf.GUARD_ONLY_COLUMNS)
    snapshot["columns"] = [c for c in snapshot["columns"] if (c["t"], c["col"]) not in removed]
    snapshot["indexes"] = [i for i in snapshot["indexes"] if (i["t"], i["col"]) not in removed]
    snapshot["foreign_keys"] = [f for f in snapshot["foreign_keys"] if (f["t"], f["col"]) not in removed]
    statements, _ = _plan(snapshot)
    assert statements == ssf.BASELINE_PLAN
