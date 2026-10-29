"""Schema de sessoes de treino e fichas nos testes: migracao 026 + o DDL que os
antigos guards de runtime (`workout_sessions._ensure_schema`,
`services._ensure_set_hr_columns`, `_ensure_workout_plans_time_column`,
`_ensure_workout_plans_split_day_column`, `_ensure_plan_link_columns`)
rodavam, reproduzido aqui SO para montar estados legados e provar que a 026
chega ao mesmo formato final.
"""

from __future__ import annotations

from typing import Any

from app import migrate as m

MIGRATION_FILENAME = "026_sessoes_fichas_schema.py"
MIGRATION_PATH = m.DEFAULT_MIGRATIONS_DIR / MIGRATION_FILENAME

# (tabela, coluna) na ordem da migracao.
OBJECT_COLUMNS = (
    ("activities", "session_status"),
    ("activities", "workout_plan_id"),
    ("exercise_sets", "hr_avg_bpm"),
    ("exercise_sets", "hr_max_bpm"),
    ("exercise_sets", "muscle_pain"),
    ("exercise_sets", "joint_pain"),
    ("exercises", "workout_plan_exercise_id"),
    ("workout_plans", "plan_time"),
    ("workout_plans", "split_day"),
    ("workout_plan_exercises", "mapping_id"),
    ("workout_plan_exercises", "preferred_equipment_id"),
)
TABLES = tuple(dict.fromkeys(table for table, _ in OBJECT_COLUMNS))

# Colunas que o baseline 001..021 NAO cria (so os guards criavam).
GUARD_ONLY_COLUMNS = (("activities", "workout_plan_id"), ("exercises", "workout_plan_exercise_id"))

# DDL exato do antigo `workout_sessions._ensure_schema` para essas duas colunas.
LEGACY_GUARD_SQL = (
    "ALTER TABLE activities ADD COLUMN workout_plan_id BIGINT NULL AFTER conducted_by, "
    "ADD KEY idx_activities_workout_plan_id (workout_plan_id), "
    "ADD CONSTRAINT fk_activities_workout_plan FOREIGN KEY (workout_plan_id) "
    "REFERENCES workout_plans(id) ON DELETE SET NULL",
    "ALTER TABLE exercises ADD COLUMN workout_plan_exercise_id BIGINT NULL AFTER mapping_id, "
    "ADD KEY idx_exercises_workout_plan_exercise_id (workout_plan_exercise_id), "
    "ADD CONSTRAINT fk_exercises_workout_plan_exercise FOREIGN KEY (workout_plan_exercise_id) "
    "REFERENCES workout_plan_exercises(id) ON DELETE SET NULL",
)

# DDL do antigo `_ensure_plan_link_columns` para mapping_id: BIGINT com sinal
# com FK para exercise_catalog(id) (BIGINT UNSIGNED) - rejeitado no MySQL 8.
LEGACY_WPE_MAPPING_SQL = (
    "ALTER TABLE workout_plan_exercises ADD COLUMN mapping_id BIGINT NULL AFTER amazfit_name, "
    "ADD KEY idx_wpe_mapping (mapping_id), ADD CONSTRAINT fk_wpe_mapping FOREIGN KEY (mapping_id) "
    "REFERENCES exercise_catalog(id) ON DELETE SET NULL"
)

# Planos esperados (texto exato gerado pela 026).
ADD_WORKOUT_PLAN_ID = (
    "ALTER TABLE activities ADD COLUMN workout_plan_id BIGINT NULL AFTER conducted_by, "
    "ADD KEY idx_activities_workout_plan_id (workout_plan_id), "
    "ADD CONSTRAINT fk_activities_workout_plan FOREIGN KEY (workout_plan_id) REFERENCES workout_plans(id) ON DELETE SET NULL"
)
ADD_WORKOUT_PLAN_EXERCISE_ID = (
    "ALTER TABLE exercises ADD COLUMN workout_plan_exercise_id BIGINT NULL AFTER mapping_id, "
    "ADD KEY idx_exercises_workout_plan_exercise_id (workout_plan_exercise_id), "
    "ADD CONSTRAINT fk_exercises_workout_plan_exercise FOREIGN KEY (workout_plan_exercise_id) "
    "REFERENCES workout_plan_exercises(id) ON DELETE SET NULL"
)
BASELINE_PLAN = [ADD_WORKOUT_PLAN_ID, ADD_WORKOUT_PLAN_EXERCISE_ID]
EMPTY_PLAN = [
    "ALTER TABLE activities ADD COLUMN session_status VARCHAR(40) NULL AFTER conducted_by, "
    "ADD KEY idx_activities_session_status (session_status)",
    ADD_WORKOUT_PLAN_ID,
    "ALTER TABLE exercise_sets ADD COLUMN hr_avg_bpm DECIMAL(6,2) NULL AFTER rest_seconds",
    "ALTER TABLE exercise_sets ADD COLUMN hr_max_bpm DECIMAL(6,2) NULL AFTER hr_avg_bpm",
    "ALTER TABLE exercise_sets ADD COLUMN muscle_pain TINYINT NULL AFTER hr_max_bpm",
    "ALTER TABLE exercise_sets ADD COLUMN joint_pain TINYINT(1) NULL AFTER muscle_pain",
    ADD_WORKOUT_PLAN_EXERCISE_ID,
    "ALTER TABLE workout_plans ADD COLUMN plan_time TIME NULL AFTER plan_date",
    "ALTER TABLE workout_plans ADD COLUMN split_day VARCHAR(1) NULL AFTER plan_type",
    "ALTER TABLE workout_plan_exercises ADD COLUMN mapping_id BIGINT UNSIGNED NULL AFTER amazfit_name, "
    "ADD KEY idx_wpe_mapping (mapping_id), ADD CONSTRAINT fk_wpe_mapping_catalog FOREIGN KEY (mapping_id) "
    "REFERENCES exercise_catalog(id) ON DELETE SET NULL",
    "ALTER TABLE workout_plan_exercises ADD COLUMN preferred_equipment_id BIGINT NULL AFTER equipment_name, "
    "ADD KEY idx_wpe_pref_equipment (preferred_equipment_id), ADD CONSTRAINT fk_wpe_pref_equipment "
    "FOREIGN KEY (preferred_equipment_id) REFERENCES gym_equipment(id) ON DELETE SET NULL",
]

# Formato final - identico na Aurora (schema extraido em 2026-09-14) e no MySQL
# de dev para estes objetos. Colunas na ordem relativa por tabela.
EXPECTED_COLUMNS = [
    ("activities", "workout_plan_id", "bigint", "YES", None),
    ("activities", "session_status", "varchar(40)", "YES", None),
    ("exercise_sets", "hr_avg_bpm", "decimal(6,2)", "YES", None),
    ("exercise_sets", "hr_max_bpm", "decimal(6,2)", "YES", None),
    ("exercise_sets", "muscle_pain", "tinyint", "YES", None),
    ("exercise_sets", "joint_pain", "tinyint(1)", "YES", None),
    ("exercises", "workout_plan_exercise_id", "bigint", "YES", None),
    ("workout_plan_exercises", "mapping_id", "bigint unsigned", "YES", None),
    ("workout_plan_exercises", "preferred_equipment_id", "bigint", "YES", None),
    ("workout_plans", "plan_time", "time", "YES", None),
    ("workout_plans", "split_day", "varchar(1)", "YES", None),
]
EXPECTED_INDEXES = [
    ("activities", "idx_activities_session_status", "session_status"),
    ("activities", "idx_activities_workout_plan_id", "workout_plan_id"),
    ("exercises", "idx_exercises_workout_plan_exercise_id", "workout_plan_exercise_id"),
    ("workout_plan_exercises", "idx_wpe_mapping", "mapping_id"),
    ("workout_plan_exercises", "idx_wpe_pref_equipment", "preferred_equipment_id"),
]
EXPECTED_FKS = [
    ("activities", "fk_activities_workout_plan", "workout_plan_id", "workout_plans", "id", "SET NULL", "NO ACTION"),
    ("exercises", "fk_exercises_workout_plan_exercise", "workout_plan_exercise_id", "workout_plan_exercises", "id", "SET NULL", "NO ACTION"),
    ("workout_plan_exercises", "fk_wpe_mapping_catalog", "mapping_id", "exercise_catalog", "id", "SET NULL", "NO ACTION"),
    ("workout_plan_exercises", "fk_wpe_pref_equipment", "preferred_equipment_id", "gym_equipment", "id", "SET NULL", "NO ACTION"),
]


def load_up():
    return m.load_python_migration(MIGRATION_PATH)


def apply_026(conn: Any, *, dry_run: bool = False) -> list[str]:
    """Roda a 026 na conexao dada (qualquer backend) e devolve os statements."""
    db = m.MigrationDB(conn, dry_run=dry_run)
    load_up()(db)
    if not dry_run:
        conn.commit()
    return db.statements


def run_sql(raw_conn: Any, statements) -> None:
    with raw_conn.cursor() as cur:
        for sql in statements:
            cur.execute(sql)


def _in(values) -> str:
    return ", ".join(["%s"] * len(values))


def drop_columns(raw_conn: Any, columns) -> None:
    """Remove as colunas dadas com as FKs e indices que as usam (estado "sem os
    objetos"), inclusive FK/indice com o nome esperado deixado em OUTRA coluna
    por um caso anterior."""
    # nome esperado de FK/indice por (tabela, coluna) - so os das colunas removidas
    fk_name = {(fk[0], fk[2]): fk[1] for fk in EXPECTED_FKS}
    index_name = {(idx[0], idx[2]): idx[1] for idx in EXPECTED_INDEXES}
    with raw_conn.cursor() as cur:
        for table, column in columns:
            name = fk_name.get((table, column), "")
            cur.execute(
                "SELECT DISTINCT CONSTRAINT_NAME FROM information_schema.KEY_COLUMN_USAGE WHERE TABLE_SCHEMA = DATABASE() "
                "AND TABLE_NAME = %s AND REFERENCED_TABLE_NAME IS NOT NULL AND (COLUMN_NAME = %s OR CONSTRAINT_NAME = %s)",
                (table, column, name),
            )
            for row in cur.fetchall():
                cur.execute(f"ALTER TABLE `{table}` DROP FOREIGN KEY `{row['CONSTRAINT_NAME']}`")
        for table, column in columns:
            names = [index_name.get((table, column), "")]
            cur.execute(
                "SELECT DISTINCT INDEX_NAME FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() "
                f"AND TABLE_NAME = %s AND INDEX_NAME <> 'PRIMARY' AND (COLUMN_NAME = %s OR INDEX_NAME IN ({_in(names)}))",
                (table, column, *names),
            )
            for row in cur.fetchall():
                cur.execute(f"ALTER TABLE `{table}` DROP INDEX `{row['INDEX_NAME']}`")
        for table, column in columns:
            cur.execute(
                "SELECT COUNT(*) AS n FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
                "AND TABLE_NAME = %s AND COLUMN_NAME = %s",
                (table, column),
            )
            if cur.fetchone()["n"]:
                cur.execute(f"ALTER TABLE `{table}` DROP COLUMN `{column}`")


def truncate_tables(raw_conn: Any) -> None:
    with raw_conn.cursor() as cur:
        cur.execute("SET FOREIGN_KEY_CHECKS = 0")
        try:
            for table in TABLES:
                cur.execute(f"TRUNCATE TABLE `{table}`")
        finally:
            cur.execute("SET FOREIGN_KEY_CHECKS = 1")


def shape(raw_conn: Any) -> dict[str, Any]:
    """Colunas (ordem relativa, tipo, nulabilidade, default), indices e FKs dos objetos da 026."""
    tables = TABLES
    names = tuple(dict.fromkeys(column for _, column in OBJECT_COLUMNS))
    wanted = set(OBJECT_COLUMNS)
    with raw_conn.cursor() as cur:
        cur.execute(
            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT FROM information_schema.COLUMNS "
            f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({_in(tables)}) AND COLUMN_NAME IN ({_in(names)}) "
            "ORDER BY TABLE_NAME, ORDINAL_POSITION",
            tables + names,
        )
        columns = [tuple(row.values()) for row in cur.fetchall() if (row["TABLE_NAME"], row["COLUMN_NAME"]) in wanted]
        cur.execute(
            "SELECT TABLE_NAME, INDEX_NAME, COLUMN_NAME FROM information_schema.STATISTICS "
            f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({_in(tables)}) AND COLUMN_NAME IN ({_in(names)}) "
            "AND INDEX_NAME <> 'PRIMARY' ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX",
            tables + names,
        )
        indexes = [tuple(row.values()) for row in cur.fetchall() if (row["TABLE_NAME"], row["COLUMN_NAME"]) in wanted]
        cur.execute(
            "SELECT k.TABLE_NAME, k.CONSTRAINT_NAME, k.COLUMN_NAME, k.REFERENCED_TABLE_NAME, k.REFERENCED_COLUMN_NAME, "
            "r.DELETE_RULE, r.UPDATE_RULE FROM information_schema.KEY_COLUMN_USAGE k "
            "JOIN information_schema.REFERENTIAL_CONSTRAINTS r ON r.CONSTRAINT_SCHEMA = k.CONSTRAINT_SCHEMA "
            "AND r.CONSTRAINT_NAME = k.CONSTRAINT_NAME AND r.TABLE_NAME = k.TABLE_NAME "
            f"WHERE k.TABLE_SCHEMA = DATABASE() AND k.TABLE_NAME IN ({_in(tables)}) AND k.COLUMN_NAME IN ({_in(names)}) "
            "ORDER BY k.TABLE_NAME, k.CONSTRAINT_NAME",
            tables + names,
        )
        fks = [tuple(row.values()) for row in cur.fetchall() if (row["TABLE_NAME"], row["COLUMN_NAME"]) in wanted]
    return {"columns": columns, "indexes": indexes, "fks": fks}


def assert_final_shape(raw_conn: Any) -> None:
    got = shape(raw_conn)
    assert got["columns"] == EXPECTED_COLUMNS
    assert got["indexes"] == EXPECTED_INDEXES
    assert got["fks"] == EXPECTED_FKS
