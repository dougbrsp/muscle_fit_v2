"""Schema de `cardio_recommendations` nos testes: migracao 025 + o DDL que o
antigo `cardio_recommendations._ensure_schema()` rodava em runtime (removido),
reproduzido aqui SO para montar estados legados e provar que a 025 chega ao
mesmo formato final.
"""

from __future__ import annotations

from typing import Any

from app import migrate as m

MIGRATION_FILENAME = "025_cardio_recommendations_schema.py"
MIGRATION_PATH = m.DEFAULT_MIGRATIONS_DIR / MIGRATION_FILENAME
TABLE = "cardio_recommendations"

# Primeira versao do guard (so o CREATE, antes de workout_plan_id/user_id).
LEGACY_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS cardio_recommendations (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  reference_date DATE NOT NULL,
  generated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  model VARCHAR(120) NULL,
  context_snapshot JSON NULL,
  recommendation_markdown MEDIUMTEXT NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_cardio_recommendations_date (reference_date)
) ENGINE=InnoDB
"""

# Guard completo, na ordem em que rodava (sem linhas: backfill nao se aplica).
LEGACY_FULL_SQL = (
    LEGACY_CREATE_SQL,
    "ALTER TABLE cardio_recommendations ADD COLUMN workout_plan_id BIGINT NULL "
    "AFTER reference_date, ADD KEY idx_cardio_recommendations_plan (workout_plan_id)",
    "ALTER TABLE cardio_recommendations ADD CONSTRAINT fk_cardio_recommendations_workout_plan "
    "FOREIGN KEY (workout_plan_id) REFERENCES workout_plans(id) ON DELETE SET NULL",
    "ALTER TABLE cardio_recommendations ADD COLUMN user_id BIGINT NULL AFTER id",
    "ALTER TABLE cardio_recommendations MODIFY COLUMN user_id BIGINT NOT NULL",
)


def load_up():
    return m.load_python_migration(MIGRATION_PATH)


def apply_025(conn: Any, *, dry_run: bool = False) -> list[str]:
    """Roda a 025 na conexao dada (qualquer backend) e devolve os statements."""
    db = m.MigrationDB(conn, dry_run=dry_run)
    load_up()(db)
    if not dry_run:
        conn.commit()
    return db.statements


def ensure_cardio_table(raw_conn: Any) -> list[str]:
    """Garante a tabela no schema de teste pelo MESMO caminho de producao (025)."""
    return apply_025(raw_conn)


def run_sql(raw_conn: Any, statements) -> None:
    with raw_conn.cursor() as cur:
        for sql in statements:
            cur.execute(sql)


def drop_table(raw_conn: Any) -> None:
    run_sql(raw_conn, [f"DROP TABLE IF EXISTS `{TABLE}`"])


def shape(raw_conn: Any) -> dict[str, Any]:
    """Colunas (ordem, tipo, nulabilidade, default, extra), indices, FKs e engine."""
    with raw_conn.cursor() as cur:
        cur.execute(
            "SELECT ORDINAL_POSITION, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, COLUMN_KEY, EXTRA "
            "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s "
            "ORDER BY ORDINAL_POSITION",
            (TABLE,),
        )
        columns = [tuple(row.values()) for row in cur.fetchall()]
        cur.execute(
            "SELECT INDEX_NAME, SEQ_IN_INDEX, COLUMN_NAME, NON_UNIQUE FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s ORDER BY INDEX_NAME, SEQ_IN_INDEX",
            (TABLE,),
        )
        indexes = [tuple(row.values()) for row in cur.fetchall()]
        cur.execute(
            "SELECT k.CONSTRAINT_NAME, k.COLUMN_NAME, k.REFERENCED_TABLE_NAME, k.REFERENCED_COLUMN_NAME, "
            "r.DELETE_RULE, r.UPDATE_RULE FROM information_schema.KEY_COLUMN_USAGE k "
            "JOIN information_schema.REFERENTIAL_CONSTRAINTS r ON r.CONSTRAINT_SCHEMA = k.CONSTRAINT_SCHEMA "
            "AND r.CONSTRAINT_NAME = k.CONSTRAINT_NAME AND r.TABLE_NAME = k.TABLE_NAME "
            "WHERE k.TABLE_SCHEMA = DATABASE() AND k.TABLE_NAME = %s ORDER BY k.CONSTRAINT_NAME",
            (TABLE,),
        )
        fks = [tuple(row.values()) for row in cur.fetchall()]
        cur.execute(
            "SELECT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
            (TABLE,),
        )
        engine = (cur.fetchone() or {}).get("ENGINE")
    return {"columns": columns, "indexes": indexes, "fks": fks, "engine": engine}


# Formato final medido no MySQL de dev (tabela criada pelo guard antigo),
# 2026-09-14 - o que a 025 precisa reproduzir.
EXPECTED_COLUMNS = [
    ("id", "bigint", "NO", None, "PRI", "auto_increment"),
    ("user_id", "bigint", "NO", None, "", ""),
    ("reference_date", "date", "NO", None, "MUL", ""),
    ("workout_plan_id", "bigint", "YES", None, "MUL", ""),
    ("generated_at", "timestamp", "NO", "CURRENT_TIMESTAMP", "", "DEFAULT_GENERATED"),
    ("model", "varchar(120)", "YES", None, "", ""),
    ("context_snapshot", "json", "YES", None, "", ""),
    ("recommendation_markdown", "mediumtext", "NO", None, "", ""),
    ("created_at", "timestamp", "NO", "CURRENT_TIMESTAMP", "", "DEFAULT_GENERATED"),
]
# Depois da cadeia inteira de migracoes (`app.migrate`): a 098 (M98 C3a) soma
# as colunas da recomendacao estruturada no fim da tabela.
EXPECTED_COLUMNS_AFTER_098 = EXPECTED_COLUMNS + [
    ("recommendation_json", "json", "YES", None, "", ""),
    ("prompt_version", "varchar(40)", "YES", None, "", ""),
]
EXPECTED_INDEXES = [
    ("idx_cardio_recommendations_date", 1, "reference_date", 1),
    ("idx_cardio_recommendations_plan", 1, "workout_plan_id", 1),
    ("PRIMARY", 1, "id", 0),
]
EXPECTED_FKS = [
    ("fk_cardio_recommendations_workout_plan", "workout_plan_id", "workout_plans", "id", "SET NULL", "NO ACTION"),
]


def assert_final_shape(raw_conn: Any, *, with_098: bool = False) -> None:
    """Formato da 025 (`with_098=False`) ou da cadeia inteira, com a 098."""
    got = shape(raw_conn)
    assert [c[1:] for c in got["columns"]] == (EXPECTED_COLUMNS_AFTER_098 if with_098 else EXPECTED_COLUMNS)
    assert got["indexes"] == EXPECTED_INDEXES
    assert got["fks"] == EXPECTED_FKS
    assert got["engine"] == "InnoDB"
