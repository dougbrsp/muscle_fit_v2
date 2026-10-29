"""Migracao 030 nos testes: plano contra o recorte da Aurora e as duas tabelas
de backup recriadas no formato REAL (colunas, indices e collation extraidos da
Aurora em 2026-09-14) para exercitar os DROPs em MySQL.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app import migrate as m

MIGRATION_FILENAME = "030_drop_tabelas_backup_onprem.py"
MIGRATION_PATH = m.DEFAULT_MIGRATIONS_DIR / MIGRATION_FILENAME
SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "unit" / "data" / "aurora_schema_2026_09_14_030.json"

BACKUP_TABLES = ("exercise_mappings_backup_20260714", "exercises_backup_before_mapping_sync")
AURORA_PLAN = [f"DROP TABLE {name}" for name in BACKUP_TABLES]


def load_snapshot() -> dict[str, Any]:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def load_up():
    return m.load_python_migration(MIGRATION_PATH)


def load_plan():
    return load_up().__globals__["plan"]


def apply_030(conn: Any, *, dry_run: bool = False) -> list[str]:
    """Roda a 030 na conexao dada (qualquer backend) e devolve os statements."""
    db = m.MigrationDB(conn, dry_run=dry_run)
    load_up()(db)
    if not dry_run:
        conn.commit()
    return db.statements


class SnapshotDB:
    """Responde as leituras de `information_schema` da 030 a partir do
    snapshot. Qualquer outra leitura (contagem de linhas) ou escrita falha."""

    def __init__(self, snapshot: dict[str, Any]):
        self.snap = snapshot
        self.queries: list[str] = []

    def query(self, sql: str, params=None) -> list[dict[str, Any]]:
        self.queries.append(sql)
        wanted = set(params or ())
        s = self.snap
        if "information_schema.TABLES" in sql:
            return [
                {"TABLE_NAME": t["table_name"], "TABLE_TYPE": t.get("table_type", "BASE TABLE")}
                for t in s["tables"]
                if t["table_name"] in wanted
            ]
        if "information_schema.KEY_COLUMN_USAGE" in sql:
            return [
                {"TABLE_NAME": f["t"], "CONSTRAINT_NAME": f["fk"], "REFERENCED_TABLE_NAME": f["ref_t"]}
                for f in s["foreign_keys"]
                if f["t"] in wanted or f["ref_t"] in wanted
            ]
        if "information_schema.VIEW_TABLE_USAGE" in sql:
            return [{"VIEW_NAME": v["view"], "TABLE_NAME": v["t"]} for v in s["view_table_usage"] if v["t"] in wanted]
        raise AssertionError(f"leitura inesperada no plano sobre o snapshot: {sql}")

    def query_one(self, sql: str, params=None):
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params=None):
        raise AssertionError(f"escrita no snapshot: {sql}")


# ---------------------------------------------------------------------------
# Tabelas no formato real (MySQL)
# ---------------------------------------------------------------------------


def _column_ddl(c: dict[str, Any]) -> str:
    parts = [f"`{c['col']}`", c["type"], "NULL" if c["nullable"] == "YES" else "NOT NULL"]
    if c["dflt"] is not None:
        parts.append("DEFAULT CURRENT_TIMESTAMP" if c["dflt"] == "CURRENT_TIMESTAMP" else "DEFAULT '" + str(c["dflt"]).replace("'", "''") + "'")
    extra = c["extra"].lower()
    if "auto_increment" in extra:
        parts.append("AUTO_INCREMENT")
    if "on update current_timestamp" in extra:
        parts.append("ON UPDATE CURRENT_TIMESTAMP")
    return " ".join(parts)


def create_table_ddl(snapshot: dict[str, Any], table: str) -> str:
    columns = sorted((c for c in snapshot["columns"] if c["t"] == table), key=lambda c: c["pos"])
    indexes: dict[str, dict[str, Any]] = {}
    for i in sorted((i for i in snapshot["indexes"] if i["t"] == table), key=lambda i: (i["idx"], i["seq"])):
        indexes.setdefault(i["idx"], {"cols": [], "unique": int(i["non_unique"]) == 0})["cols"].append(f"`{i['col']}`")
    keys = []
    for name, idx in sorted(indexes.items()):
        cols = ", ".join(idx["cols"])
        if name == "PRIMARY":
            keys.append(f"PRIMARY KEY ({cols})")
        elif name.startswith("ft_"):
            keys.append(f"FULLTEXT KEY `{name}` ({cols})")
        else:
            keys.append(f"{'UNIQUE ' if idx['unique'] else ''}KEY `{name}` ({cols})")
    collation = next(t["collation"] for t in snapshot["tables"] if t["table_name"] == table)
    body = ",\n  ".join([_column_ddl(c) for c in columns] + keys)
    return f"CREATE TABLE `{table}` (\n  {body}\n) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE={collation}"


def create_backup_tables(raw_conn: Any, tables=BACKUP_TABLES) -> None:
    """Cria as tabelas no formato da Aurora, com linhas (so testes)."""
    snapshot = load_snapshot()
    with raw_conn.cursor() as cur:
        for table in tables:
            cur.execute(f"DROP TABLE IF EXISTS `{table}`")
            cur.execute(create_table_ddl(snapshot, table))
        if BACKUP_TABLES[0] in tables:
            cur.execute(
                f"INSERT INTO `{BACKUP_TABLES[0]}` (exercise_name, amazfit_name, keywords) "
                "VALUES ('Supino reto', 'Bench Press', 'peito'), ('Remada curvada', 'Bent-over Row', 'costas')"
            )
        if BACKUP_TABLES[1] in tables:
            cur.execute(
                f"INSERT INTO `{BACKUP_TABLES[1]}` (activity_id, sequence, original_name, mapping_id) "
                "VALUES (1, 1, 'Supino', 114), (1, 2, 'Remada', NULL)"
            )
    raw_conn.commit()


def real_format(raw_conn: Any) -> dict[str, Any]:
    placeholders = ", ".join(["%s"] * len(BACKUP_TABLES))
    with raw_conn.cursor() as cur:
        cur.execute(
            "SELECT TABLE_NAME, ORDINAL_POSITION, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, COLUMN_KEY, EXTRA "
            f"FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({placeholders})",
            BACKUP_TABLES,
        )
        columns = sorted(
            (r["TABLE_NAME"], int(r["ORDINAL_POSITION"]), r["COLUMN_NAME"], r["COLUMN_TYPE"], r["IS_NULLABLE"], r["COLUMN_DEFAULT"], r["COLUMN_KEY"], r["EXTRA"])
            for r in cur.fetchall()
        )
        cur.execute(
            "SELECT TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX, COLUMN_NAME, NON_UNIQUE FROM information_schema.STATISTICS "
            f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({placeholders})",
            BACKUP_TABLES,
        )
        indexes = sorted((r["TABLE_NAME"], r["INDEX_NAME"], int(r["SEQ_IN_INDEX"]), r["COLUMN_NAME"], int(r["NON_UNIQUE"])) for r in cur.fetchall())
        cur.execute(
            f"SELECT TABLE_NAME, TABLE_COLLATION FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({placeholders})",
            BACKUP_TABLES,
        )
        collations = sorted((r["TABLE_NAME"], r["TABLE_COLLATION"]) for r in cur.fetchall())
    return {"columns": columns, "indexes": indexes, "collations": collations}


def snapshot_format(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "columns": sorted((c["t"], c["pos"], c["col"], c["type"], c["nullable"], c["dflt"], c["ckey"], c["extra"]) for c in snapshot["columns"]),
        "indexes": sorted((i["t"], i["idx"], int(i["seq"]), i["col"], int(i["non_unique"])) for i in snapshot["indexes"]),
        "collations": sorted((t["table_name"], t["collation"]) for t in snapshot["tables"] if t["table_name"] in BACKUP_TABLES),
    }


def existing_backup_tables(raw_conn: Any) -> list[str]:
    placeholders = ", ".join(["%s"] * len(BACKUP_TABLES))
    with raw_conn.cursor() as cur:
        cur.execute(
            f"SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({placeholders})",
            BACKUP_TABLES,
        )
        return sorted(r["TABLE_NAME"] for r in cur.fetchall())
