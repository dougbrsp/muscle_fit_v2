"""Migracoes 032 (colunas de seguranca) e 033 (matriz de tecnicas) nos testes:
recorte da Aurora, `SnapshotDB` com `COLLATION_NAME` e aplicacao em qualquer
backend.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from app import migrate as m
from tests.support.paridade_028 import SnapshotDB

MIGRATION_032 = m.DEFAULT_MIGRATIONS_DIR / "032_equipment_safety_columns.py"
MIGRATION_033 = m.DEFAULT_MIGRATIONS_DIR / "033_equipment_technique_compatibility.py"
SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "unit" / "data" / "aurora_schema_2026_09_17_032_033.json"

ALTER_EQUIPMENT = (
    "ALTER TABLE equipment ADD COLUMN max_safe_rpe TINYINT NULL, "
    "ADD COLUMN load_increment_kg DECIMAL(5,2) NULL, ADD COLUMN safety_notes TEXT NULL"
)
ALTER_EQUIPMENT_EXERCISES = (
    "ALTER TABLE equipment_exercises ADD COLUMN max_safe_rpe_override TINYINT NULL, ADD COLUMN safety_notes TEXT NULL"
)
NEW_TABLES = ("equipment_technique_compatibility", "equipment_exercise_technique_overrides")


def load_snapshot() -> dict[str, Any]:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def module(path: Path) -> dict[str, Any]:
    return m.load_python_migration(path).__globals__


def without_advanced_techniques(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Recorte SEM `advanced_techniques`: banco em que a 031 ainda esta
    pendente no mesmo lote (ambiente novo; era o estado da Aurora ate o deploy
    de 2026-09-17)."""
    snap = copy.deepcopy(snapshot)
    snap["tables"] = [t for t in snap["tables"] if t["table_name"] != "advanced_techniques"]
    snap["columns"] = [c for c in snap["columns"] if c["t"] != "advanced_techniques"]
    return snap


class SafetySnapshotDB(SnapshotDB):
    """`SnapshotDB` + `COLLATION_NAME` em COLUMNS, filtro por tabela em
    KEY_COLUMN_USAGE e o atributo `dry_run` da `MigrationDB`."""

    def __init__(self, snapshot: dict[str, Any], dry_run: bool = False):
        super().__init__(snapshot)
        self.dry_run = dry_run

    def query(self, sql: str, params=None) -> list[dict[str, Any]]:
        rows = super().query(sql, params)
        wanted = set(params or ())
        if "information_schema.COLUMNS" in sql:
            collations = {(c["t"], c["col"]): c.get("coll") for c in self.snap["columns"]}
            for row in rows:
                row["COLLATION_NAME"] = collations.get((row["TABLE_NAME"], row["COLUMN_NAME"]))
        if "KEY_COLUMN_USAGE" in sql:
            rows = [row for row in rows if row["TABLE_NAME"] in wanted]
        return rows


def apply(conn: Any, path: Path, *, dry_run: bool = False) -> list[str]:
    """Roda a migracao na conexao dada (qualquer backend) e devolve os statements."""
    db = m.MigrationDB(conn, dry_run=dry_run)
    m.load_python_migration(path)(db)
    if not dry_run:
        conn.commit()
    return db.statements
