"""Migracao 037 (congelamento da prescricao) nos testes: recorte da Aurora,
estado convergido sobre o recorte e aplicacao em qualquer backend."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from app import migrate as m

MIGRATION_037 = m.DEFAULT_MIGRATIONS_DIR / "037_prescricao_congelada.py"
SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "unit" / "data" / "aurora_schema_2026_09_18_037.json"

WPE = "workout_plan_exercises"
SETS = "exercise_sets"
DECISION_INDEX = "idx_wpe_progression_decision"

ALTER_WPE = (
    f"ALTER TABLE {WPE} ADD COLUMN prescription_json JSON NULL, "
    "ADD COLUMN progression_decision_id BIGINT NULL, "
    "ADD COLUMN engine_version VARCHAR(40) NULL, "
    "ADD COLUMN requires_user_choice TINYINT(1) NOT NULL DEFAULT 0, "
    f"ADD KEY {DECISION_INDEX} (progression_decision_id)"
)
ALTER_SETS = f"ALTER TABLE {SETS} ADD COLUMN rpe_target_effective TINYINT NULL, ADD COLUMN safety_event VARCHAR(40) NULL"

# tabela -> [(coluna, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, COLUMN_KEY)]
AFTER_037 = {
    WPE: [
        ("prescription_json", "json", "YES", None, ""),
        ("progression_decision_id", "bigint", "YES", None, "MUL"),
        ("engine_version", "varchar(40)", "YES", None, ""),
        ("requires_user_choice", "tinyint(1)", "NO", "0", ""),
    ],
    SETS: [
        ("rpe_target_effective", "tinyint", "YES", None, ""),
        ("safety_event", "varchar(40)", "YES", None, ""),
    ],
}


def load_snapshot() -> dict[str, Any]:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def module() -> dict[str, Any]:
    return m.load_python_migration(MIGRATION_037).__globals__


def with_columns(snapshot: dict[str, Any], *tables: str) -> dict[str, Any]:
    """Recorte com as colunas (e o indice) da 037 ja aplicadas."""
    snap = copy.deepcopy(snapshot)
    for table in tables or (WPE, SETS):
        last = max(c["pos"] for c in snap["columns"] if c["t"] == table)
        for offset, (col, type_, nullable, dflt, ckey) in enumerate(AFTER_037[table], start=1):
            snap["columns"].append(
                {
                    "t": table,
                    "pos": last + offset,
                    "col": col,
                    "type": type_,
                    "nullable": nullable,
                    "dflt": dflt,
                    "ckey": ckey,
                    "extra": "",
                    "coll": None,
                }
            )
        if table == WPE:
            snap["indexes"].append(
                {"t": WPE, "idx": DECISION_INDEX, "seq": 1, "col": "progression_decision_id", "non_unique": 1}
            )
    snap["columns"].sort(key=lambda c: (c["t"], c["pos"]))
    snap["indexes"].sort(key=lambda i: (i["t"], i["idx"], i["seq"]))
    return snap


def apply(conn: Any, *, dry_run: bool = False) -> list[str]:
    """Roda a 037 na conexao dada (qualquer backend) e devolve os statements."""
    db = m.MigrationDB(conn, dry_run=dry_run)
    m.load_python_migration(MIGRATION_037)(db)
    if not dry_run:
        conn.commit()
    return db.statements
