"""Schema do grupo B (integracoes, metricas, relatorios) nos testes: migracao
027 + o DDL que os antigos `_ensure_schema()` rodavam em runtime (removidos),
reproduzido aqui SO para montar estados legados e provar que a 027 chega ao
mesmo formato final - que e o formato real da Aurora de producao, lido de
`aurora_schema_2026_09_14_grupo_b.json` (information_schema, somente leitura).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from app import migrate as m

MIGRATION_FILENAME = "027_integracoes_metricas_schema.py"
MIGRATION_PATH = m.DEFAULT_MIGRATIONS_DIR / MIGRATION_FILENAME
AURORA_SNAPSHOT_PATH = Path(__file__).with_name("aurora_schema_2026_09_14_grupo_b.json")

TABLES = (
    "devices",
    "sleep_metrics",
    "manual_effort_metrics",
    "intervals_activities",
    "intervals_wellness",
    "reports",
)
# Fora do baseline 001..021 (so existiam por causa dos guards).
NON_BASELINE_TABLES = TABLES[1:]


def load_module():
    """Carrega a 027 como o runner (`app.migrate.load_python_migration`): por
    caminho, sem registrar em sys.modules e sem __pycache__ em db/migrations."""
    spec = importlib.util.spec_from_file_location("migration_027_grupo_b", MIGRATION_PATH)
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def aurora_snapshot() -> dict[str, Any]:
    return json.loads(AURORA_SNAPSHOT_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# DDL legado (antigos guards)
# ---------------------------------------------------------------------------

# 001_schema.sql (v7): devices sem user_id.
DEVICES_BASELINE_SQL = """
CREATE TABLE IF NOT EXISTS devices (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  device_name VARCHAR(160) NOT NULL,
  platform VARCHAR(80) NOT NULL DEFAULT 'zepp',
  token_hash CHAR(64) NOT NULL,
  paired_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_seen_at TIMESTAMP NULL,
  revoked_at TIMESTAMP NULL,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  UNIQUE KEY uq_devices_token_hash (token_hash),
  KEY idx_devices_active (is_active)
) ENGINE=InnoDB
"""

SLEEP_V1_SQL = """
CREATE TABLE IF NOT EXISTS sleep_metrics (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  local_date DATE NOT NULL UNIQUE,
  duration_hours DECIMAL(4,2) NULL,
  regularity_percent DECIMAL(5,2) NULL,
  deep_sleep_hours DECIMAL(4,2) NULL,
  rem_sleep_hours DECIMAL(4,2) NULL,
  awake_count INT NULL,
  awake_hours DECIMAL(4,2) NULL,
  notes TEXT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB
"""

MANUAL_V1_SQL = """
CREATE TABLE IF NOT EXISTS manual_effort_metrics (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  local_date DATE NOT NULL UNIQUE,
  epoc_training_load INT NULL,
  pai_score INT NULL,
  notes TEXT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB
"""

# Antes da Parte 1 (sem user_id).
INTERVALS_ACTIVITIES_V1_SQL = """
CREATE TABLE IF NOT EXISTS intervals_activities (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    external_id VARCHAR(191) NOT NULL,
    activity_date DATE NOT NULL,
    start_time DATETIME NULL,
    name VARCHAR(255) NULL,
    sport_type VARCHAR(80) NULL,
    moving_time_seconds INT NULL,
    elapsed_time_seconds INT NULL,
    distance_meters DECIMAL(12,2) NULL,
    calories_kcal DECIMAL(10,2) NULL,
    avg_hr_bpm DECIMAL(6,2) NULL,
    max_hr_bpm DECIMAL(6,2) NULL,
    training_load DECIMAL(10,2) NULL,
    intensity DECIMAL(10,2) NULL,
    raw_json JSON NULL,
    synced_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_intervals_activities_external_id (external_id),
    KEY idx_intervals_activities_date (activity_date),
    KEY idx_intervals_activities_sport_type (sport_type)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
"""

INTERVALS_WELLNESS_V1_SQL = """
CREATE TABLE IF NOT EXISTS intervals_wellness (
    wellness_date DATE NOT NULL,
    fitness_ctl DECIMAL(10,2) NULL,
    fatigue_atl DECIMAL(10,2) NULL,
    form_tsb DECIMAL(10,2) NULL,
    ramp_rate DECIMAL(10,2) NULL,
    weight_kg DECIMAL(10,2) NULL,
    resting_hr_bpm DECIMAL(6,2) NULL,
    hrv_ms DECIMAL(10,2) NULL,
    sleep_seconds INT NULL,
    raw_json JSON NULL,
    synced_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (wellness_date)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
"""

# Guard atual (CREATE com user_id NULL ja embutido).
INTERVALS_ACTIVITIES_GUARD_SQL = INTERVALS_ACTIVITIES_V1_SQL.replace(
    "PRIMARY KEY,\n    external_id", "PRIMARY KEY,\n    user_id BIGINT NULL,\n    external_id"
)
INTERVALS_WELLNESS_GUARD_SQL = INTERVALS_WELLNESS_V1_SQL.replace(
    "wellness_date DATE NOT NULL,\n", "wellness_date DATE NOT NULL,\n    user_id BIGINT NULL,\n"
)

REPORTS_SQL = """
CREATE TABLE IF NOT EXISTS reports (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id BIGINT NOT NULL,
  status ENUM('generating','done','failed') NOT NULL DEFAULT 'generating',
  period_days INT NOT NULL DEFAULT 90,
  requested_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  completed_at TIMESTAMP NULL,
  model VARCHAR(120) NULL,
  pdf_path VARCHAR(255) NULL,
  error_message TEXT NULL,
  context_snapshot JSON NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_reports_user (user_id),
  KEY idx_reports_status (status)
) ENGINE=InnoDB
"""

# Estado final que os guards completos produziam num banco vazio (ordem em que rodavam).
LEGACY_GUARD_FULL_SQL: dict[str, tuple[str, ...]] = {
    "devices": (
        "ALTER TABLE devices ADD COLUMN user_id BIGINT NULL AFTER id",
        "ALTER TABLE devices MODIFY COLUMN user_id BIGINT NOT NULL",
    ),
    "sleep_metrics": (
        SLEEP_V1_SQL,
        "ALTER TABLE sleep_metrics ADD COLUMN user_id BIGINT NULL AFTER id",
        "ALTER TABLE sleep_metrics MODIFY COLUMN user_id BIGINT NOT NULL",
        "ALTER TABLE sleep_metrics DROP INDEX local_date",
        "ALTER TABLE sleep_metrics ADD UNIQUE KEY uq_sleep_metrics_user_date (user_id, local_date)",
    ),
    "manual_effort_metrics": (
        MANUAL_V1_SQL,
        "ALTER TABLE manual_effort_metrics ADD COLUMN user_id BIGINT NULL AFTER id",
        "ALTER TABLE manual_effort_metrics MODIFY COLUMN user_id BIGINT NOT NULL",
        "ALTER TABLE manual_effort_metrics DROP INDEX local_date",
        "ALTER TABLE manual_effort_metrics ADD UNIQUE KEY uq_manual_effort_metrics_user_date (user_id, local_date)",
    ),
    "intervals_activities": (
        INTERVALS_ACTIVITIES_GUARD_SQL,
        "ALTER TABLE intervals_activities MODIFY COLUMN user_id BIGINT NOT NULL",
        "ALTER TABLE intervals_activities DROP INDEX uq_intervals_activities_external_id",
        "ALTER TABLE intervals_activities ADD UNIQUE KEY uq_intervals_activities_user_external (user_id, external_id)",
    ),
    "intervals_wellness": (
        INTERVALS_WELLNESS_GUARD_SQL,
        "ALTER TABLE intervals_wellness ADD COLUMN readiness_score DECIMAL(6,2) NULL",
        "ALTER TABLE intervals_wellness ADD COLUMN daily_effort_load INT NULL",
        "ALTER TABLE intervals_wellness MODIFY COLUMN user_id BIGINT NOT NULL",
        "ALTER TABLE intervals_wellness DROP PRIMARY KEY, ADD PRIMARY KEY (user_id, wellness_date)",
    ),
    "reports": (REPORTS_SQL,),
}


def run_sql(raw_conn: Any, statements) -> None:
    with raw_conn.cursor() as cur:
        for sql in statements:
            cur.execute(sql)


def reset_to_baseline(raw_conn: Any) -> None:
    """Grupo B no estado do baseline 001..021: `devices` sem `user_id` e as
    demais tabelas ausentes."""
    with raw_conn.cursor() as cur:
        cur.execute("SET FOREIGN_KEY_CHECKS = 0")
        try:
            for table in TABLES:
                cur.execute(f"DROP TABLE IF EXISTS `{table}`")
            cur.execute(DEVICES_BASELINE_SQL)
        finally:
            cur.execute("SET FOREIGN_KEY_CHECKS = 1")


def apply_legacy_guard_full(raw_conn: Any, tables=TABLES) -> None:
    for table in tables:
        run_sql(raw_conn, LEGACY_GUARD_FULL_SQL[table])


def apply_027(conn: Any, *, dry_run: bool = False) -> list[str]:
    """Roda a 027 na conexao dada (qualquer backend) e devolve os statements."""
    db = m.MigrationDB(conn, dry_run=dry_run)
    m.load_python_migration(MIGRATION_PATH)(db)
    if not dry_run:
        conn.commit()
    return db.statements


def ensure_grupo_b_tables(raw_conn: Any) -> list[str]:
    """Garante as tabelas do grupo B pelo MESMO caminho de producao (027)."""
    return apply_027(raw_conn)


# ---------------------------------------------------------------------------
# Formato
# ---------------------------------------------------------------------------


def shape(raw_conn: Any, table: str) -> dict[str, Any]:
    """Colunas (ordem, tipo, nulabilidade, default, key, extra), indices, FKs, engine e collation."""
    with raw_conn.cursor() as cur:
        cur.execute(
            "SELECT ORDINAL_POSITION, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, COLUMN_KEY, EXTRA "
            "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s "
            "ORDER BY ORDINAL_POSITION",
            (table,),
        )
        columns = [
            (r["ORDINAL_POSITION"], r["COLUMN_NAME"], r["COLUMN_TYPE"], r["IS_NULLABLE"], r["COLUMN_DEFAULT"], r["COLUMN_KEY"], r["EXTRA"])
            for r in cur.fetchall()
        ]
        cur.execute(
            "SELECT INDEX_NAME, SEQ_IN_INDEX, COLUMN_NAME, NON_UNIQUE FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
            (table,),
        )
        indexes = sorted((r["INDEX_NAME"], r["SEQ_IN_INDEX"], r["COLUMN_NAME"], int(r["NON_UNIQUE"])) for r in cur.fetchall())
        cur.execute(
            "SELECT CONSTRAINT_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME "
            "FROM information_schema.KEY_COLUMN_USAGE WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s "
            "AND REFERENCED_TABLE_NAME IS NOT NULL ORDER BY CONSTRAINT_NAME",
            (table,),
        )
        fks = [tuple(r.values()) for r in cur.fetchall()]
        cur.execute(
            "SELECT ENGINE, TABLE_COLLATION FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
            (table,),
        )
        row = cur.fetchone() or {}
    return {
        "columns": columns,
        "indexes": indexes,
        "fks": fks,
        "engine": row.get("ENGINE"),
        "collation": row.get("TABLE_COLLATION"),
    }


def all_shapes(raw_conn: Any) -> dict[str, dict[str, Any]]:
    return {table: shape(raw_conn, table) for table in TABLES}


def aurora_shape(table: str) -> dict[str, Any]:
    snap = aurora_snapshot()
    columns = [
        (c["pos"], c["col"], c["type"], c["nullable"], c["dflt"], c["ckey"], c["extra"])
        for c in sorted((c for c in snap["columns"] if c["t"] == table), key=lambda c: c["pos"])
    ]
    indexes = sorted((i["idx"], i["seq"], i["col"], int(i["non_unique"])) for i in snap["indexes"] if i["t"] == table)
    fks = [(f["fk"], f["col"], f["ref_t"], f["ref_col"]) for f in snap["foreign_keys"] if f["t"] == table]
    info = next(t for t in snap["tables"] if t["table_name"] == table)
    return {"columns": columns, "indexes": indexes, "fks": fks, "engine": info["engine"], "collation": info["collation"]}


def assert_aurora_shape(raw_conn: Any, tables=TABLES) -> None:
    for table in tables:
        assert shape(raw_conn, table) == aurora_shape(table), table


# ---------------------------------------------------------------------------
# Banco falso a partir do snapshot (plano da 027 "na Aurora", sem rede)
# ---------------------------------------------------------------------------


class SnapshotDB:
    """Responde SO as leituras de `information_schema` que `plan()` faz, com os
    dados do snapshot. Qualquer outra consulta (contagem de linhas, admin) ou
    escrita falha: prova que, na Aurora, o plano nao precisa nem ler dados."""

    def __init__(self, snapshot: dict[str, Any] | None = None):
        self.snap = snapshot or aurora_snapshot()
        self.queries: list[str] = []

    def table_exists(self, table: str) -> bool:
        return any(t["table_name"] == table for t in self.snap["tables"])

    def query(self, sql: str, params=None) -> list[dict[str, Any]]:
        self.queries.append(sql)
        table = params[0] if params else None
        if "information_schema.COLUMNS" in sql:
            return [
                {
                    "COLUMN_NAME": c["col"],
                    "COLUMN_TYPE": c["type"],
                    "IS_NULLABLE": c["nullable"],
                    "COLUMN_DEFAULT": c["dflt"],
                    "COLUMN_KEY": c["ckey"],
                    "EXTRA": c["extra"],
                }
                for c in sorted((c for c in self.snap["columns"] if c["t"] == table), key=lambda c: c["pos"])
            ]
        if "information_schema.STATISTICS" in sql:
            return [
                {"INDEX_NAME": i["idx"], "COLUMN_NAME": i["col"], "NON_UNIQUE": i["non_unique"]}
                for i in sorted((i for i in self.snap["indexes"] if i["t"] == table), key=lambda i: (i["idx"], i["seq"]))
            ]
        if "information_schema.TABLES" in sql and "ENGINE" in sql:
            return [{"ENGINE": t["engine"]} for t in self.snap["tables"] if t["table_name"] == table]
        raise AssertionError(f"consulta inesperada no plano sobre o snapshot: {sql}")

    def query_one(self, sql: str, params=None):
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params=None):
        raise AssertionError(f"escrita no snapshot: {sql}")
