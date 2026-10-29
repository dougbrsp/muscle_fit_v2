"""Apoio dos testes de integracao/dataapi da migracao
`023_mapping_fks_exercise_catalog.py`: volta o schema ao estado "001..021"
(FKs legadas para `exercise_zepp_mappings`, `mapping_id` BIGINT signed), semeia
um exercicio que so existe no catalogo canonico e roda o cenario sync + espelho
legado. Todas as funcoes recebem uma conexao PyMySQL crua (DictCursor,
autocommit) - ver `tests/support/dataapi_backend.raw_mysql`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

MIGRATION_FILENAME = "023_mapping_fks_exercise_catalog.py"

# (tabela, FK legada do 001_schema.sql, FK canonica criada pela 023)
TABLES = (
    ("gym_equipment", "fk_gym_equipment_mapping", "fk_gym_equipment_mapping_catalog"),
    ("equipment_exercise_mappings", "fk_eem_mapping", "fk_eem_mapping_catalog"),
)

SEED_TABLES = (
    "gym_equipment",
    "gym_equipment_catalog",
    "equipment_exercises",
    "equipment_exercise_mappings",
    "exercise_catalog",
    "exercise_zepp_mappings",
    "equipment",
    "gyms",
    "users",
)

LEGACY_EXERCISE_ID = 1001  # existe nos dois catalogos (mesmo id)
CATALOG_ONLY_ID = 2001  # so em exercise_catalog


def scalar(conn: Any, sql: str, params: tuple = ()) -> Any:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        return None if row is None else next(iter(row.values()))


def mapping_fks(conn: Any, table: str) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT k.CONSTRAINT_NAME AS name, k.REFERENCED_TABLE_NAME AS ref_table, "
            "k.REFERENCED_COLUMN_NAME AS ref_column, r.DELETE_RULE AS delete_rule, r.UPDATE_RULE AS update_rule "
            "FROM information_schema.KEY_COLUMN_USAGE k JOIN information_schema.REFERENTIAL_CONSTRAINTS r "
            "ON r.CONSTRAINT_SCHEMA = k.CONSTRAINT_SCHEMA AND r.TABLE_NAME = k.TABLE_NAME AND r.CONSTRAINT_NAME = k.CONSTRAINT_NAME "
            "WHERE k.TABLE_SCHEMA = DATABASE() AND k.TABLE_NAME = %s AND k.COLUMN_NAME = 'mapping_id' "
            "AND k.REFERENCED_TABLE_NAME IS NOT NULL ORDER BY k.CONSTRAINT_NAME",
            (table,),
        )
        return list(cur.fetchall())


def column_type(conn: Any, table: str, column: str = "mapping_id") -> str:
    return scalar(
        conn,
        "SELECT COLUMN_TYPE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
        "AND TABLE_NAME = %s AND COLUMN_NAME = %s",
        (table, column),
    )


def truncate_seed_tables(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute("SET FOREIGN_KEY_CHECKS = 0")
        try:
            for table in SEED_TABLES:
                cur.execute(f"TRUNCATE TABLE `{table}`")
        finally:
            cur.execute("SET FOREIGN_KEY_CHECKS = 1")


def restore_legacy_mapping_fks(conn: Any) -> None:
    """Desfaz a 023: FKs de volta para exercise_zepp_mappings (nomes e regras
    do 001_schema.sql) e `mapping_id` BIGINT signed."""
    with conn.cursor() as cur:
        for table, legacy_name, _ in TABLES:
            fks = mapping_fks(conn, table)
            if [(fk["name"], fk["ref_table"]) for fk in fks] == [(legacy_name, "exercise_zepp_mappings")] and column_type(
                conn, table
            ) == "bigint":
                continue
            for fk in fks:
                cur.execute(f"ALTER TABLE {table} DROP FOREIGN KEY `{fk['name']}`")
            if column_type(conn, table) != "bigint":
                cur.execute(f"ALTER TABLE {table} MODIFY COLUMN mapping_id BIGINT NOT NULL")
            cur.execute(
                f"ALTER TABLE {table} ADD CONSTRAINT {legacy_name} FOREIGN KEY (mapping_id) "
                "REFERENCES exercise_zepp_mappings(id) ON DELETE CASCADE"
            )


def reset_to_pre_023(conn: Any, control_tables: tuple[str, ...]) -> None:
    """Sem a tabela de controle o runner reexecuta tudo a partir da 022 sobre
    o schema existente: antes, volta a forma anterior das migracoes que mudam
    o que uma anterior confere (068 x 036, 075 x 047)."""
    # Import tardio: `migrations` importa pymysql no topo.
    from tests.support.migrations import undo_reshaping_migrations

    undo_reshaping_migrations(conn)
    with conn.cursor() as cur:
        for table in control_tables:
            cur.execute(f"DROP TABLE IF EXISTS `{table}`")
    truncate_seed_tables(conn)
    restore_legacy_mapping_fks(conn)


@dataclass
class Seed:
    gym_id: int
    bench_id: int  # academia possui; serve LEGACY_EXERCISE_ID e CATALOG_ONLY_ID
    cable_id: int  # academia possui; sem vinculos ate o teste criar


def seed(conn: Any) -> Seed:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES ('fk023-admin', 'x', 'admin')")
        user_id = cur.lastrowid
        cur.execute("INSERT INTO gyms (name, owner_user_id) VALUES ('Academia FK023', %s)", (user_id,))
        gym_id = cur.lastrowid
        cur.execute(
            "INSERT INTO exercise_zepp_mappings (id, exercise_name, amazfit_name) VALUES (%s, 'Supino', 'Supino')",
            (LEGACY_EXERCISE_ID,),
        )
        cur.execute("INSERT INTO exercise_catalog (id, name) VALUES (%s, 'Supino')", (LEGACY_EXERCISE_ID,))
        cur.execute("INSERT INTO exercise_catalog (id, name) VALUES (%s, 'So no catalogo')", (CATALOG_ONLY_ID,))
        cur.execute("INSERT INTO equipment (name) VALUES ('Banco FK023')")
        bench_id = cur.lastrowid
        cur.execute("INSERT INTO equipment (name) VALUES ('Polia FK023')")
        cable_id = cur.lastrowid
        for exercise_id in (LEGACY_EXERCISE_ID, CATALOG_ONLY_ID):
            cur.execute(
                "INSERT INTO equipment_exercises (equipment_id, exercise_id) VALUES (%s, %s)", (bench_id, exercise_id)
            )
        for equipment_id in (bench_id, cable_id):
            cur.execute(
                "INSERT INTO gym_equipment_catalog (gym_id, equipment_id) VALUES (%s, %s)", (gym_id, equipment_id)
            )
    return Seed(gym_id=gym_id, bench_id=bench_id, cable_id=cable_id)


@dataclass
class Outcome:
    sync_warnings: list[dict[str, Any]]
    link_warnings: list[dict[str, Any]]
    legacy_mirror: str
    gym_rows: list[tuple[int, int, int]]
    mirrored_rows: int


def clear_runtime_caches() -> None:
    from app import equipment_exercises, gym_equipment_sync, load_conventions

    gym_equipment_sync.reset_fk_ref_cache()
    equipment_exercises.reset_legacy_fk_cache()
    # a deteccao de `equipment_exercises.load_convention` (045) e cacheada por
    # schema; aqui o MESMO schema muda de estado no meio do teste (a migracao
    # roda entre as duas metades), entao a deteccao precisa ser refeita.
    load_conventions.reset_load_convention_column_cache()


def run_sync_and_link(transaction: Callable[[], Any], raw: Any, data: Seed) -> Outcome:
    """Sync da academia + vinculo (Polia, exercicio so-do-catalogo) pela
    camada da aplicacao (`transaction` = `app.db.transaction` do backend em
    uso); estado conferido pela conexao crua."""
    from app.equipment_exercises import upsert_equipment_exercise
    from app.gym_equipment_sync import sync_gym_equipment_for_gym

    clear_runtime_caches()
    with transaction() as conn:
        sync_warnings = sync_gym_equipment_for_gym(conn, data.gym_id)
    with transaction() as conn:
        link = upsert_equipment_exercise(conn, data.cable_id, CATALOG_ONLY_ID)
    with raw.cursor() as cur:
        cur.execute(
            "SELECT mapping_id, equipment_id, is_active FROM gym_equipment WHERE gym_id = %s ORDER BY mapping_id, equipment_id",
            (data.gym_id,),
        )
        rows = [(int(r["mapping_id"]), int(r["equipment_id"]), int(r["is_active"])) for r in cur.fetchall()]
    mirrored = scalar(
        raw,
        "SELECT COUNT(*) FROM equipment_exercise_mappings WHERE equipment_id = %s AND mapping_id = %s AND is_active = 1",
        (data.cable_id, CATALOG_ONLY_ID),
    )
    return Outcome(sync_warnings, link["gym_sync_warnings"], link["legacy_mirror"], rows, int(mirrored))
