"""Migracao 034 (`user_equipment_preferences`) nos testes: recorte da Aurora e
aplicacao em qualquer backend."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app import migrate as m

MIGRATION_034 = m.DEFAULT_MIGRATIONS_DIR / "034_user_equipment_preferences.py"
SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "unit" / "data" / "aurora_schema_2026_09_17_034.json"
TABLE = "user_equipment_preferences"

EXPECTED_COLUMNS = {
    "user_id": ("bigint", "NO"),
    "equipment_id": ("bigint", "NO"),
    "stars": ("tinyint", "NO"),
    "updated_at": ("timestamp", "NO"),
}


def load_snapshot() -> dict[str, Any]:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def module() -> dict[str, Any]:
    return m.load_python_migration(MIGRATION_034).__globals__


def apply(conn: Any, *, dry_run: bool = False) -> list[str]:
    """Roda a 034 na conexao dada (qualquer backend) e devolve os statements."""
    db = m.MigrationDB(conn, dry_run=dry_run)
    m.load_python_migration(MIGRATION_034)(db)
    if not dry_run:
        conn.commit()
    return db.statements
