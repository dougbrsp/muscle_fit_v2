"""Migracao 038 (auditoria da periodizacao) nos testes: recorte da Aurora,
estado convergido sobre o recorte e aplicacao em qualquer backend."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from app import migrate as m

MIGRATION_038 = m.DEFAULT_MIGRATIONS_DIR / "038_periodizacao_rascunhos.py"
SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "unit" / "data" / "aurora_schema_2026_09_19_038.json"

LOGS = "template_generation_logs"
TEMPLATES = "workout_plan_templates"
GENERATION_LOG_INDEX = "idx_wpt_generation_log"
LOGS_USER_INDEX = "idx_tgl_user_created"
LOGS_TEMPLATE_INDEX = "idx_tgl_saved_template"

ALTER_TEMPLATES = (
    f"ALTER TABLE {TEMPLATES} ADD COLUMN origin ENUM('manual','periodization') NOT NULL DEFAULT 'manual', "
    "ADD COLUMN generation_log_id BIGINT NULL, "
    f"ADD KEY {GENERATION_LOG_INDEX} (generation_log_id)"
)

# colunas que a 038 acrescenta a workout_plan_templates:
# (coluna, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, COLUMN_KEY)
AFTER_038_TEMPLATES = [
    ("origin", "enum('manual','periodization')", "NO", "manual", ""),
    ("generation_log_id", "bigint", "YES", None, "MUL"),
]

# template_generation_logs como a 038 a cria:
# (coluna, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, COLUMN_KEY, EXTRA)
AFTER_038_LOGS = [
    ("id", "bigint", "NO", None, "PRI", "auto_increment"),
    ("user_id", "bigint", "NO", None, "MUL", ""),
    ("request_json", "json", "YES", None, "", ""),
    ("rag_sources", "json", "YES", None, "", ""),
    ("model", "varchar(80)", "YES", None, "", ""),
    ("prompt_version", "varchar(40)", "YES", None, "", ""),
    ("status", "varchar(20)", "NO", "pending", "", ""),
    ("validation_report", "json", "YES", None, "", ""),
    ("tokens_in", "int", "YES", None, "", ""),
    ("tokens_out", "int", "YES", None, "", ""),
    ("cost_usd", "decimal(8,4)", "YES", None, "", ""),
    ("saved_template_id", "bigint", "YES", None, "MUL", ""),
    ("created_at", "timestamp", "NO", "CURRENT_TIMESTAMP", "", "DEFAULT_GENERATED"),
]

LOGS_INDEXES = [
    ("PRIMARY", 1, "id", 0),
    (LOGS_USER_INDEX, 1, "user_id", 1),
    (LOGS_USER_INDEX, 2, "created_at", 1),
    (LOGS_TEMPLATE_INDEX, 1, "saved_template_id", 1),
]

LOGS_FOREIGN_KEYS = [
    ("fk_tgl_user", "user_id", "users", "id", "CASCADE"),
    ("fk_tgl_saved_template", "saved_template_id", TEMPLATES, "id", "SET NULL"),
]


def load_snapshot() -> dict[str, Any]:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def module() -> dict[str, Any]:
    return m.load_python_migration(MIGRATION_038).__globals__


def with_logs_table(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Recorte com `template_generation_logs` ja criada pela 038."""
    snap = copy.deepcopy(snapshot)
    snap["tables"].append({"table_name": LOGS, "engine": "InnoDB", "collation": "utf8mb4_0900_ai_ci"})
    snap["tables"].sort(key=lambda t: t["table_name"])
    for pos, (col, type_, nullable, dflt, ckey, extra) in enumerate(AFTER_038_LOGS, start=1):
        snap["columns"].append(
            {
                "t": LOGS,
                "pos": pos,
                "col": col,
                "type": type_,
                "nullable": nullable,
                "dflt": dflt,
                "ckey": ckey,
                "extra": extra,
                "coll": "utf8mb4_0900_ai_ci" if type_.startswith("varchar") else None,
            }
        )
    for idx, seq, col, non_unique in LOGS_INDEXES:
        snap["indexes"].append({"t": LOGS, "idx": idx, "seq": seq, "col": col, "non_unique": non_unique})
    for fk, col, ref_t, ref_col, on_delete in LOGS_FOREIGN_KEYS:
        snap["foreign_keys"].append(
            {
                "t": LOGS,
                "fk": fk,
                "col": col,
                "ref_t": ref_t,
                "ref_col": ref_col,
                "on_delete": on_delete,
                "on_update": "NO ACTION",
            }
        )
    return _sorted(snap)


def with_template_columns(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Recorte com as duas colunas novas (e o indice) de workout_plan_templates."""
    snap = copy.deepcopy(snapshot)
    last = max(c["pos"] for c in snap["columns"] if c["t"] == TEMPLATES)
    for offset, (col, type_, nullable, dflt, ckey) in enumerate(AFTER_038_TEMPLATES, start=1):
        snap["columns"].append(
            {
                "t": TEMPLATES,
                "pos": last + offset,
                "col": col,
                "type": type_,
                "nullable": nullable,
                "dflt": dflt,
                "ckey": ckey,
                "extra": "",
                "coll": "utf8mb4_0900_ai_ci" if type_.startswith("enum") else None,
            }
        )
    snap["indexes"].append(
        {"t": TEMPLATES, "idx": GENERATION_LOG_INDEX, "seq": 1, "col": "generation_log_id", "non_unique": 1}
    )
    return _sorted(snap)


def converged(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Recorte com a 038 inteira aplicada."""
    return with_template_columns(with_logs_table(snapshot))


def _sorted(snap: dict[str, Any]) -> dict[str, Any]:
    snap["columns"].sort(key=lambda c: (c["t"], c["pos"]))
    snap["indexes"].sort(key=lambda i: (i["t"], i["idx"], i["seq"]))
    return snap


def apply(conn: Any, *, dry_run: bool = False) -> list[str]:
    """Roda a 038 na conexao dada (qualquer backend) e devolve os statements."""
    db = m.MigrationDB(conn, dry_run=dry_run)
    m.load_python_migration(MIGRATION_038)(db)
    if not dry_run:
        conn.commit()
    return db.statements
