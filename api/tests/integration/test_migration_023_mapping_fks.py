"""Migracao 023 (FKs de mapping_id -> exercise_catalog) contra MySQL real, pelo
runner `app.migrate` no backend pymysql.

Schema proprio (`<TEST_MYSQL_DATABASE sem _test>_fk023_test`) com 001..021
aplicadas (estado real de dev/CI); o runner registra o baseline e aplica
022/023. Antes da 023 o sync exclui o exercicio que so existe no catalogo e o
espelho legado e pulado; depois, o mesmo exercicio e materializado e espelhado.
"""

from __future__ import annotations

import os

import pytest

from app import migrate as m
from app.equipment_exercises import MIRROR_OK, MIRROR_SKIPPED_FK_TARGET_MISSING
from app.gym_equipment_sync import FK_LEGACY_EXCLUDED_CODE
from tests.support import fk023
from tests.support.dataapi_backend import raw_mysql
from tests.support.migrations import recreate_schema_with_migrations

LEGACY, CATALOG_ONLY = fk023.LEGACY_EXERCISE_ID, fk023.CATALOG_ONLY_ID


def _migrations_from(version: int) -> list[str]:
    """Migracoes a partir de `version`, derivadas de api/db/migrations/ (lista
    fixa conflitaria a cada nova migracao)."""
    return [mig.filename for mig in m.discover_migrations(m.DEFAULT_MIGRATIONS_DIR) if mig.version >= version]


def _kwargs() -> dict:
    return dict(
        host=os.environ["MYSQL_HOST"],
        port=int(os.environ["MYSQL_PORT"]),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
    )


@pytest.fixture(scope="module")
def fk_schema() -> str:
    base = os.environ["MYSQL_DATABASE"]
    database = f"{base[: -len('_test')]}_fk023_test"
    recreate_schema_with_migrations(database=database, **_kwargs())
    return database


@pytest.fixture
def raw(fk_schema):
    with raw_mysql(database=fk_schema, **_kwargs()) as conn:
        # o runner reexecuta tudo a partir da 023: `reset_to_pre_023` volta
        # antes a forma anterior das RESHAPING_MIGRATIONS (068 x 036, 075 x 047)
        fk023.reset_to_pre_023(conn, (m.CONTROL_TABLE, m.LOCK_TABLE))
        yield conn


@pytest.fixture
def app_db(monkeypatch, fk_schema, raw):
    """Camada da aplicacao (pymysql) apontada para o schema da 023."""
    from app import equipment_exercises, gym_equipment_sync

    monkeypatch.setenv("MYSQL_DATABASE", fk_schema)
    monkeypatch.delenv("MIGRATIONS_DIR", raising=False)
    gym_equipment_sync.reset_fk_ref_cache()
    equipment_exercises.reset_legacy_fk_cache()
    from app.db import transaction

    return transaction


def _assert_legacy_state(raw) -> None:
    for table, legacy_name, _ in fk023.TABLES:
        assert [(fk["name"], fk["ref_table"]) for fk in fk023.mapping_fks(raw, table)] == [
            (legacy_name, "exercise_zepp_mappings")
        ]
        assert fk023.column_type(raw, table) == "bigint"


def _assert_canonical_state(raw) -> None:
    for table, _, canonical_name in fk023.TABLES:
        assert [
            (fk["name"], fk["ref_table"], fk["ref_column"], fk["delete_rule"]) for fk in fk023.mapping_fks(raw, table)
        ] == [(canonical_name, "exercise_catalog", "id", "CASCADE")]
        assert fk023.column_type(raw, table) == "bigint unsigned"


def test_antes_da_023_exclui_do_sync_e_pula_espelho_depois_materializa_e_espelha(app_db, raw):
    data = fk023.seed(raw)
    _assert_legacy_state(raw)

    before = fk023.run_sync_and_link(app_db, raw, data)
    assert [w["code"] for w in before.sync_warnings] == [FK_LEGACY_EXCLUDED_CODE]
    assert before.sync_warnings[0]["excluded_exercise_ids"] == [CATALOG_ONLY]
    assert before.sync_warnings[0]["fk_referenced_table"] == "exercise_zepp_mappings"
    assert before.legacy_mirror == MIRROR_SKIPPED_FK_TARGET_MISSING
    assert before.link_warnings and before.mirrored_rows == 0
    assert before.gym_rows == [(LEGACY, data.bench_id, 1)]

    report = m.migrate()
    assert [a.filename for a in report.applied] == _migrations_from(22)
    assert fk023.MIGRATION_FILENAME in _migrations_from(22)
    _assert_canonical_state(raw)

    after = fk023.run_sync_and_link(app_db, raw, data)
    assert after.sync_warnings == [] and after.link_warnings == []
    assert after.legacy_mirror == MIRROR_OK
    assert after.mirrored_rows == 1
    assert after.gym_rows == [
        (LEGACY, data.bench_id, 1),
        (CATALOG_ONLY, data.bench_id, 1),
        (CATALOG_ONLY, data.cable_id, 1),
    ]

    # Reexecucao da 023 (direto e pelo runner): no-op.
    from app.db import get_conn

    conn = get_conn()
    try:
        db = m.MigrationDB(conn)
        m.load_python_migration(m.DEFAULT_MIGRATIONS_DIR / fk023.MIGRATION_FILENAME)(db)
        assert db.statements == []
    finally:
        conn.close()
    assert m.migrate().applied == []
    _assert_canonical_state(raw)


def test_dry_run_lista_ddl_sem_alterar_schema(app_db, raw):
    fk023.seed(raw)
    report = m.migrate(dry_run=True)
    planned = {a.filename: a.statements for a in report.applied}
    assert planned[fk023.MIGRATION_FILENAME] == [
        "ALTER TABLE gym_equipment DROP FOREIGN KEY `fk_gym_equipment_mapping`",
        "ALTER TABLE gym_equipment MODIFY COLUMN mapping_id BIGINT UNSIGNED NOT NULL",
        "ALTER TABLE gym_equipment ADD CONSTRAINT fk_gym_equipment_mapping_catalog FOREIGN KEY (mapping_id) "
        "REFERENCES exercise_catalog (id) ON DELETE CASCADE ON UPDATE NO ACTION",
        "ALTER TABLE equipment_exercise_mappings DROP FOREIGN KEY `fk_eem_mapping`",
        "ALTER TABLE equipment_exercise_mappings MODIFY COLUMN mapping_id BIGINT UNSIGNED NOT NULL",
        "ALTER TABLE equipment_exercise_mappings ADD CONSTRAINT fk_eem_mapping_catalog FOREIGN KEY (mapping_id) "
        "REFERENCES exercise_catalog (id) ON DELETE CASCADE ON UPDATE NO ACTION",
    ]
    _assert_legacy_state(raw)


def test_orfaos_falham_antes_do_ddl_e_nao_registram(app_db, raw):
    data = fk023.seed(raw)
    with raw.cursor() as cur:
        # permitido pela FK legada, mas sem linha em exercise_catalog
        cur.execute("INSERT INTO exercise_zepp_mappings (id, exercise_name, amazfit_name) VALUES (3001, 'Legado', 'Legado')")
        cur.execute(
            "INSERT INTO gym_equipment (gym_id, mapping_id, equipment_id) VALUES (%s, 3001, %s)",
            (data.gym_id, data.bench_id),
        )

    with pytest.raises(m.MigrationExecutionError, match=r"023_mapping_fks_exercise_catalog\.py: 023: gym_equipment\.mapping_id tem 1 linha"):
        m.migrate()

    _assert_legacy_state(raw)  # nenhuma das duas tabelas foi tocada
    recorded = {r["filename"] for r in _records(raw)}
    assert "022_equipment_types_parity.py" in recorded and fk023.MIGRATION_FILENAME not in recorded
    assert fk023.scalar(raw, f"SELECT COUNT(*) FROM {m.LOCK_TABLE}") == 0

    with raw.cursor() as cur:
        cur.execute("INSERT INTO exercise_catalog (id, name) VALUES (3001, 'Legado')")
    assert [a.filename for a in m.migrate().applied] == _migrations_from(23)
    assert _migrations_from(23)[0] == fk023.MIGRATION_FILENAME
    _assert_canonical_state(raw)


def test_retoma_apos_falha_parcial(app_db, raw):
    fk023.seed(raw)
    with raw.cursor() as cur:
        # simula execucao interrompida depois do DROP + MODIFY em gym_equipment
        cur.execute("ALTER TABLE gym_equipment DROP FOREIGN KEY fk_gym_equipment_mapping")
        cur.execute("ALTER TABLE gym_equipment MODIFY COLUMN mapping_id BIGINT UNSIGNED NOT NULL")
    report = m.migrate()
    statements = next(a.statements for a in report.applied if a.filename == fk023.MIGRATION_FILENAME)
    assert statements[0].startswith("ALTER TABLE gym_equipment ADD CONSTRAINT fk_gym_equipment_mapping_catalog")
    _assert_canonical_state(raw)


def _records(conn):
    with conn.cursor() as cur:
        cur.execute(f"SELECT filename FROM {m.CONTROL_TABLE}")
        return cur.fetchall()
