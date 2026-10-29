"""Migracao 035 (`user_equipment_exercise_preferences`, estrelas por PAR
exercicio+equipamento) nos testes: recorte da Aurora e aplicacao em qualquer
backend. Para reproduzir o deploy real (034 e 035 sobem juntas) use
`apply_034_then_035`."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app import migrate as m
from tests.support import preferences_034

MIGRATION_035 = m.DEFAULT_MIGRATIONS_DIR / "035_user_equipment_exercise_preferences.py"
SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "unit" / "data" / "aurora_schema_2026_09_17_035.json"
TABLE = "user_equipment_exercise_preferences"
OLD_TABLE = preferences_034.TABLE

EXPECTED_COLUMNS = {
    "user_id": ("bigint", "NO"),
    "equipment_exercise_id": ("bigint", "NO"),
    "stars": ("tinyint", "NO"),
    "updated_at": ("timestamp", "NO"),
}


def load_snapshot() -> dict[str, Any]:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def module() -> dict[str, Any]:
    return m.load_python_migration(MIGRATION_035).__globals__


def apply(conn: Any, *, dry_run: bool = False) -> list[str]:
    """Roda a 035 na conexao dada (qualquer backend) e devolve os statements."""
    db = m.MigrationDB(conn, dry_run=dry_run)
    m.load_python_migration(MIGRATION_035)(db)
    if not dry_run:
        conn.commit()
    return db.statements


def apply_034_then_035(conn: Any) -> list[str]:
    """Sequencia real do deploy; devolve os statements das duas, na ordem."""
    return preferences_034.apply(conn) + apply(conn)
