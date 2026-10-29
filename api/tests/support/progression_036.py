"""Migracao 036 (estado do Motor de Progressao v2) nos testes: recorte da
Aurora e aplicacao em qualquer backend."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from app import migrate as m

MIGRATION_036 = m.DEFAULT_MIGRATIONS_DIR / "036_progression_state.py"
SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "unit" / "data" / "aurora_schema_2026_09_17_036.json"

GROUP_STATES = "progression_group_states"
TRACKS = "progression_tracks"
DECISIONS = "progression_decisions"
NEW_TABLES = (GROUP_STATES, TRACKS, DECISIONS)


def load_snapshot() -> dict[str, Any]:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def module() -> dict[str, Any]:
    return m.load_python_migration(MIGRATION_036).__globals__


def apply(conn: Any, *, dry_run: bool = False) -> list[str]:
    """Roda a 036 na conexao dada (qualquer backend) e devolve os statements."""
    db = m.MigrationDB(conn, dry_run=dry_run)
    m.load_python_migration(MIGRATION_036)(db)
    if not dry_run:
        conn.commit()
    return db.statements


# ---------------------------------------------------------------------------
# Estado "depois da 036" sobre o recorte, para os cenarios convergidos
# ---------------------------------------------------------------------------

# tabela -> (colunas [(nome, type, ckey)], indices [(nome, [cols], non_unique)],
#            fks [(nome, coluna, tabela referenciada)])
AFTER_036: dict[str, tuple] = {
    GROUP_STATES: (
        [
            ("id", "bigint", "PRI"),
            ("user_id", "bigint", "MUL"),
            ("muscle_group_id", "bigint", "MUL"),
            ("phase", "enum('F1','F2','F3','F4','F5','F6')", ""),
            (
                "phase_status",
                "enum('ACTIVE','SKIPPED_NO_ELIGIBLE_TECHNIQUE','SKIPPED_LOAD_NOT_APPLICABLE','BLOCKED_ACTIVATION_MAX')",
                "",
            ),
            ("reps_min", "tinyint", ""),
            ("reps_max", "tinyint", ""),
            ("pending_action", "varchar(40)", ""),
            ("pending_payload", "json", ""),
            ("phase_entered_at", "datetime", ""),
            ("last_rep_range_change_at", "datetime", ""),
            ("last_load_change_at", "datetime", ""),
            ("last_sets_change_at", "datetime", ""),
            ("technique_applied_at", "datetime", ""),
            ("next_eligible_progression_at", "datetime", ""),
            ("engine_version", "varchar(40)", ""),
            ("updated_at", "timestamp", ""),
        ],
        [
            ("PRIMARY", ["id"], 0),
            ("uk_pgs_user_group", ["user_id", "muscle_group_id"], 0),
            ("idx_pgs_group", ["muscle_group_id"], 1),
        ],
        [("fk_pgs_user", "user_id", "users"), ("fk_pgs_group", "muscle_group_id", "muscle_groups")],
    ),
    TRACKS: (
        [
            ("id", "bigint", "PRI"),
            ("user_id", "bigint", "MUL"),
            ("exercise_id", "bigint unsigned", "MUL"),
            ("equipment_id", "bigint", "MUL"),
            ("muscle_group_id", "bigint", "MUL"),
            ("load_status", "enum('CALIBRATION','ACTIVE','LOAD_NOT_APPLICABLE')", ""),
            ("working_load_kg", "decimal(6,2)", ""),
            ("sets_count", "tinyint", ""),
            ("technique_code", "varchar(20)", ""),
            ("pending_action_applied_at", "datetime", ""),
            ("last_load_change_at", "datetime", ""),
            ("last_sets_change_at", "datetime", ""),
            ("last_executed_at", "datetime", ""),
            ("engine_version", "varchar(40)", ""),
            ("updated_at", "timestamp", ""),
        ],
        [
            ("PRIMARY", ["id"], 0),
            ("uk_ptr_user_pair", ["user_id", "exercise_id", "equipment_id"], 0),
            ("idx_ptr_user_group_executed", ["user_id", "muscle_group_id", "last_executed_at"], 1),
            ("idx_ptr_exercise", ["exercise_id"], 1),
            ("idx_ptr_equipment", ["equipment_id"], 1),
        ],
        [
            ("fk_ptr_user", "user_id", "users"),
            ("fk_ptr_equipment", "equipment_id", "equipment"),
            ("fk_ptr_group", "muscle_group_id", "muscle_groups"),
        ],
    ),
    DECISIONS: (
        [
            ("id", "bigint", "PRI"),
            ("group_state_id", "bigint", "MUL"),
            ("track_id", "bigint", "MUL"),
            ("user_id", "bigint", "MUL"),
            ("kind", "enum('EVALUATION','PRESCRIPTION')", ""),
            ("activity_id", "bigint", "MUL"),
            ("workout_plan_exercise_id", "bigint", "MUL"),
            ("target_datetime", "datetime", ""),
            ("phase_before", "varchar(2)", ""),
            ("phase_after", "varchar(2)", ""),
            ("decision", "varchar(40)", ""),
            ("output_json", "json", ""),
            ("evidence_json", "json", ""),
            ("blocked_by", "json", ""),
            ("engine_version", "varchar(40)", ""),
            ("created_at", "timestamp", ""),
        ],
        [
            ("PRIMARY", ["id"], 0),
            ("idx_pd_group_created", ["group_state_id", "created_at"], 1),
            ("idx_pd_track_created", ["track_id", "created_at"], 1),
            ("idx_pd_wpe", ["workout_plan_exercise_id"], 1),
            ("idx_pd_user_created", ["user_id", "created_at"], 1),
            ("idx_pd_activity", ["activity_id"], 1),
        ],
        [
            ("fk_pd_group_state", "group_state_id", GROUP_STATES),
            ("fk_pd_track", "track_id", TRACKS),
            ("fk_pd_user", "user_id", "users"),
            ("fk_pd_activity", "activity_id", "activities"),
            ("fk_pd_wpe", "workout_plan_exercise_id", "workout_plan_exercises"),
        ],
    ),
}

# colunas NOT NULL de cada tabela nova (o resto e NULL)
NOT_NULL = {
    GROUP_STATES: {
        "id",
        "user_id",
        "muscle_group_id",
        "phase",
        "phase_status",
        "reps_min",
        "reps_max",
        "phase_entered_at",
        "engine_version",
        "updated_at",
    },
    TRACKS: {"id", "user_id", "exercise_id", "equipment_id", "load_status", "sets_count", "engine_version", "updated_at"},
    DECISIONS: {"id", "group_state_id", "user_id", "kind", "decision", "engine_version", "created_at"},
}


def with_tables(snapshot: dict[str, Any], *tables: str) -> dict[str, Any]:
    """Recorte com as tabelas novas indicadas ja criadas no formato esperado."""
    snap = copy.deepcopy(snapshot)
    for table in tables or NEW_TABLES:
        columns, indexes, fks = AFTER_036[table]
        snap["tables"].append({"table_name": table, "engine": "InnoDB", "collation": "utf8mb4_0900_ai_ci"})
        for pos, (col, type_, ckey) in enumerate(columns, start=1):
            snap["columns"].append(
                {
                    "t": table,
                    "pos": pos,
                    "col": col,
                    "type": type_,
                    "nullable": "NO" if col in NOT_NULL[table] else "YES",
                    "dflt": None,
                    "ckey": ckey,
                    "extra": "auto_increment" if col == "id" else "",
                    "coll": None,
                }
            )
        for idx, cols, non_unique in indexes:
            for seq, col in enumerate(cols, start=1):
                snap["indexes"].append({"t": table, "idx": idx, "seq": seq, "col": col, "non_unique": non_unique})
        for fk, col, ref_t in fks:
            snap["foreign_keys"].append(
                {
                    "t": table,
                    "fk": fk,
                    "col": col,
                    "ref_t": ref_t,
                    "ref_col": "id",
                    "on_delete": "CASCADE",
                    "on_update": "NO ACTION",
                }
            )
    return snap
