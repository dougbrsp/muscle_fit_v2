"""Migracao 028 nos testes: plano contra o recorte da Aurora, formato final
comparado com a Aurora e volta ao formato de dev/CI (001..027) para repetir
cenarios no mesmo schema.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from app import migrate as m

MIGRATION_FILENAME = "028_catalogo_paridade_aurora.py"
MIGRATION_PATH = m.DEFAULT_MIGRATIONS_DIR / MIGRATION_FILENAME
SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "unit" / "data" / "aurora_schema_2026_09_14_028.json"

# (tabela, coluna, FK na Aurora, regra ON DELETE) - ordem da migracao.
CATALOG_FKS = (
    ("equipment_exercise_mappings", "mapping_id", "fk_eem_mapping_catalog", "CASCADE"),
    ("equipment_exercises", "exercise_id", "fk_eqex_exercise", "CASCADE"),
    ("exercise_aliases", "exercise_id", "fk_exercise_aliases_exercise_catalog", "CASCADE"),
    ("exercise_external_mappings", "exercise_id", "fk_exm_exercise", "CASCADE"),
    ("exercise_legacy_id_map", "canonical_exercise_id", "fk_elim_canonical", "NO ACTION"),
    ("exercise_muscle_contributions", "exercise_id", "fk_emc_exercise_catalog", "CASCADE"),
    ("exercises", "mapping_id", "fk_exercises_mapping_catalog", "SET NULL"),
    ("gym_equipment", "mapping_id", "fk_gym_equipment_mapping_catalog", "CASCADE"),
    ("workout_plan_exercises", "mapping_id", "fk_wpe_mapping_catalog", "SET NULL"),
    ("workout_plan_template_slot_items", "mapping_id", "fk_wptsi_mapping", "SET NULL"),
)
TABLES = tuple(sorted({"exercise_catalog", *(table for table, _, _, _ in CATALOG_FKS)}))

# Nomes/regras em dev/CI (001..027) que diferem da Aurora.
DEV_FK_NAMES = {
    "exercise_legacy_id_map": ("fk_exercise_legacy_id_map_canonical", "CASCADE"),
    "exercises": ("fk_exercises_mapping", "SET NULL"),
}
DEV_LEGACY_INDEX = "idx_exercise_legacy_id_map_canonical"

# Divergencia de dev/CI que a 028 NAO resolve; removida pela 029
# (tests/integration/dataapi/test_migration_029_aliases_sem_unique_global.py).
# Os testes da 028 param na 028, entao o indice ainda existe aqui.
PENDING_INDEXES = {("exercise_aliases", "uq_exercise_aliases_alias")}

# Plano exato num banco de dev/CI (001..027).
DEV_CI_PLAN = (
    [
        f"ALTER TABLE {table} DROP FOREIGN KEY {DEV_FK_NAMES.get(table, (name,))[0]}"
        for table, _, name, _ in CATALOG_FKS
    ]
    + [
        "ALTER TABLE exercises MODIFY COLUMN mapping_id BIGINT UNSIGNED NULL",
        "ALTER TABLE exercise_catalog MODIFY COLUMN id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT",
        f"ALTER TABLE exercise_legacy_id_map RENAME INDEX {DEV_LEGACY_INDEX} TO fk_elim_canonical",
    ]
    + [
        f"ALTER TABLE {table} ADD CONSTRAINT {name} FOREIGN KEY ({column}) REFERENCES exercise_catalog(id) ON DELETE {rule}"
        for table, column, name, rule in CATALOG_FKS
    ]
)


def load_snapshot() -> dict[str, Any]:
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def load_up():
    return m.load_python_migration(MIGRATION_PATH)


def load_plan():
    return load_up().__globals__["plan"]


def apply_028(conn: Any, *, dry_run: bool = False) -> list[str]:
    """Roda a 028 na conexao dada (qualquer backend) e devolve os statements."""
    db = m.MigrationDB(conn, dry_run=dry_run)
    load_up()(db)
    if not dry_run:
        conn.commit()
    return db.statements


# ---------------------------------------------------------------------------
# Snapshot (Aurora) e o mesmo snapshot no formato de dev/CI
# ---------------------------------------------------------------------------


def dev_ci_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Recorte da Aurora com as divergencias do catalogo medidas em dev/CI (001..027)."""
    snap = copy.deepcopy(snapshot)
    for c in snap["columns"]:
        if (c["t"], c["col"]) == ("exercise_catalog", "id"):
            c["extra"] = ""
        if (c["t"], c["col"]) == ("exercises", "mapping_id"):
            c["type"] = "bigint"
    for i in snap["indexes"]:
        if (i["t"], i["idx"]) == ("exercise_legacy_id_map", "fk_elim_canonical"):
            i["idx"] = DEV_LEGACY_INDEX
    for f in snap["foreign_keys"]:
        if f["t"] == "exercise_legacy_id_map" and f["fk"] == "fk_elim_canonical":
            f["fk"], f["on_delete"] = DEV_FK_NAMES["exercise_legacy_id_map"]
        if f["t"] == "exercises" and f["fk"] == "fk_exercises_mapping_catalog":
            f["fk"], f["ref_t"] = "fk_exercises_mapping", "exercise_zepp_mappings"
    return snap


class SnapshotDB:
    """Responde as leituras de `information_schema` da 028 a partir do
    snapshot. Contagem de linhas so com `counts` (trecho do SQL -> n); sem
    ele, qualquer contagem ou escrita falha - prova que, na Aurora, o plano
    nem le dados."""

    def __init__(self, snapshot: dict[str, Any], counts: dict[str, int] | None = None):
        self.snap = snapshot
        self.counts = counts
        self.queries: list[str] = []

    def query(self, sql: str, params=None) -> list[dict[str, Any]]:
        self.queries.append(sql)
        wanted = set(params or ())
        s = self.snap
        if "information_schema.TABLES" in sql:
            return [{"TABLE_NAME": t["table_name"], "ENGINE": t["engine"]} for t in s["tables"] if t["table_name"] in wanted]
        if "information_schema.COLUMNS" in sql:
            return [
                {
                    "TABLE_NAME": c["t"],
                    "COLUMN_NAME": c["col"],
                    "COLUMN_TYPE": c["type"],
                    "IS_NULLABLE": c["nullable"],
                    "COLUMN_DEFAULT": c["dflt"],
                    "COLUMN_KEY": c["ckey"],
                    "EXTRA": c["extra"],
                    "COLUMN_COMMENT": "",
                }
                for c in sorted(s["columns"], key=lambda c: (c["t"], c["pos"]))
                if c["t"] in wanted
            ]
        if "information_schema.STATISTICS" in sql:
            return [
                {"TABLE_NAME": i["t"], "INDEX_NAME": i["idx"], "COLUMN_NAME": i["col"], "NON_UNIQUE": i["non_unique"]}
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
                    "UPDATE_RULE": f["on_update"],
                }
                for f in s["foreign_keys"]
            ]
        if self.counts is not None and sql.startswith("SELECT COUNT(*)"):
            return [{"n": next((n for part, n in self.counts.items() if part in sql), 0)}]
        raise AssertionError(f"leitura inesperada no plano sobre o snapshot: {sql}")

    def query_one(self, sql: str, params=None):
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params=None):
        raise AssertionError(f"escrita no snapshot: {sql}")


# ---------------------------------------------------------------------------
# Formato (posicao de coluna nao conta)
# ---------------------------------------------------------------------------


def _norm_type(value: str) -> str:
    return " ".join(str(value).lower().split())


def shape(raw_conn: Any) -> dict[str, Any]:
    tables = TABLES
    placeholders = ", ".join(["%s"] * len(tables))
    with raw_conn.cursor() as cur:
        cur.execute(
            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, EXTRA FROM information_schema.COLUMNS "
            f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({placeholders})",
            tables,
        )
        columns = sorted(
            (r["TABLE_NAME"], r["COLUMN_NAME"], _norm_type(r["COLUMN_TYPE"]), r["IS_NULLABLE"], r["COLUMN_DEFAULT"], r["EXTRA"])
            for r in cur.fetchall()
        )
        cur.execute(
            "SELECT TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX, COLUMN_NAME, NON_UNIQUE FROM information_schema.STATISTICS "
            f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({placeholders})",
            tables,
        )
        indexes = sorted(
            (r["TABLE_NAME"], r["INDEX_NAME"], int(r["SEQ_IN_INDEX"]), r["COLUMN_NAME"], int(r["NON_UNIQUE"]))
            for r in cur.fetchall()
            if (r["TABLE_NAME"], r["INDEX_NAME"]) not in PENDING_INDEXES
        )
        cur.execute(
            "SELECT k.TABLE_NAME, k.CONSTRAINT_NAME, k.COLUMN_NAME, k.REFERENCED_TABLE_NAME, k.REFERENCED_COLUMN_NAME, "
            "r.DELETE_RULE, r.UPDATE_RULE FROM information_schema.KEY_COLUMN_USAGE k "
            "JOIN information_schema.REFERENTIAL_CONSTRAINTS r ON r.CONSTRAINT_SCHEMA = k.CONSTRAINT_SCHEMA "
            "AND r.CONSTRAINT_NAME = k.CONSTRAINT_NAME AND r.TABLE_NAME = k.TABLE_NAME "
            f"WHERE k.TABLE_SCHEMA = DATABASE() AND k.TABLE_NAME IN ({placeholders}) AND k.REFERENCED_TABLE_NAME IS NOT NULL",
            tables,
        )
        fks = sorted(tuple(r.values()) for r in cur.fetchall())
    return {"columns": columns, "indexes": indexes, "fks": fks}


def snapshot_shape(snapshot: dict[str, Any]) -> dict[str, Any]:
    columns = sorted(
        (c["t"], c["col"], _norm_type(c["type"]), c["nullable"], c["dflt"], c["extra"]) for c in snapshot["columns"] if c["t"] in TABLES
    )
    indexes = sorted((i["t"], i["idx"], int(i["seq"]), i["col"], int(i["non_unique"])) for i in snapshot["indexes"] if i["t"] in TABLES)
    fks = sorted(
        (f["t"], f["fk"], f["col"], f["ref_t"], f["ref_col"], f["on_delete"], f["on_update"])
        for f in snapshot["foreign_keys"]
        if f["t"] in TABLES
    )
    return {"columns": columns, "indexes": indexes, "fks": fks}


def assert_aurora_shape(raw_conn: Any) -> None:
    got, expected = shape(raw_conn), snapshot_shape(load_snapshot())
    for key in ("columns", "indexes", "fks"):
        assert got[key] == expected[key], key


def revert_to_dev_ci(raw_conn: Any) -> None:
    """Desfaz a 028 (so testes): volta ao formato medido em dev/CI 001..027."""
    with raw_conn.cursor() as cur:
        for table, _, name, _ in CATALOG_FKS:
            cur.execute(
                "SELECT CONSTRAINT_NAME FROM information_schema.KEY_COLUMN_USAGE WHERE TABLE_SCHEMA = DATABASE() "
                "AND TABLE_NAME = %s AND REFERENCED_TABLE_NAME IS NOT NULL AND CONSTRAINT_NAME IN (%s, %s)",
                (table, name, DEV_FK_NAMES.get(table, (name,))[0]),
            )
            for row in cur.fetchall():
                cur.execute(f"ALTER TABLE `{table}` DROP FOREIGN KEY `{row['CONSTRAINT_NAME']}`")
        cur.execute("ALTER TABLE exercise_catalog MODIFY COLUMN id BIGINT UNSIGNED NOT NULL")
        cur.execute("ALTER TABLE exercises MODIFY COLUMN mapping_id BIGINT NULL")
        cur.execute(
            "SELECT COUNT(*) AS n FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() "
            "AND TABLE_NAME = 'exercise_legacy_id_map' AND INDEX_NAME = 'fk_elim_canonical'"
        )
        if cur.fetchone()["n"]:
            cur.execute(f"ALTER TABLE exercise_legacy_id_map RENAME INDEX fk_elim_canonical TO {DEV_LEGACY_INDEX}")
        for table, column, name, rule in CATALOG_FKS:
            dev_name, dev_rule = DEV_FK_NAMES.get(table, (name, rule))
            ref = "exercise_zepp_mappings" if table == "exercises" else "exercise_catalog"
            cur.execute(
                f"ALTER TABLE `{table}` ADD CONSTRAINT `{dev_name}` FOREIGN KEY (`{column}`) REFERENCES `{ref}`(id) ON DELETE {dev_rule}"
            )
