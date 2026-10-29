"""Migracao 023 pelo caminho de PRODUCAO do banco: `app.migrate` + shim
`app/db_dataapi.py` + dublê da RDS Data API, num schema proprio
(`<TEST_MYSQL_DATABASE sem _test>_fk023_dataapi_test`, 001..021 aplicadas).

Mesmo resultado do backend pymysql (tests/integration/test_migration_023_mapping_fks.py):
antes da 023 o exercicio so-do-catalogo e excluido do sync e o espelho legado
e pulado; depois ele e materializado/espelhado; reexecucao e no-op; cada
statement vai numa chamada `ExecuteStatement` separada.
"""

from __future__ import annotations

import os

import pytest

from app import migrate as m
from app.equipment_exercises import MIRROR_OK, MIRROR_SKIPPED_FK_TARGET_MISSING
from tests.support import fk023
from tests.support.dataapi_backend import raw_mysql, use_dataapi
from tests.support.migrations import recreate_schema_with_migrations


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
    database = f"{base[: -len('_test')]}_fk023_dataapi_test"
    recreate_schema_with_migrations(database=database, **_kwargs())
    return database


@pytest.fixture
def raw(fk_schema):
    with raw_mysql(database=fk_schema, **_kwargs()) as conn:
        fk023.reset_to_pre_023(conn, (m.CONTROL_TABLE, m.LOCK_TABLE))
        yield conn


@pytest.fixture
def fake(monkeypatch, fake_rds, fk_schema, raw):
    from app import equipment_exercises, gym_equipment_sync

    use_dataapi(monkeypatch, fake_rds, fk_schema)
    monkeypatch.delenv("MIGRATIONS_DIR", raising=False)
    gym_equipment_sync.reset_fk_ref_cache()
    equipment_exercises.reset_legacy_fk_cache()
    yield fake_rds
    assert fake_rds.open_transactions == [], "deixou transacao Data API aberta"


def _transaction():
    import app.db as db  # atributo trocado por use_dataapi

    return db.transaction()


def test_023_via_data_api_reaponta_fks_e_libera_sync_e_espelho(fake, raw):
    data = fk023.seed(raw)

    before = fk023.run_sync_and_link(_transaction, raw, data)
    assert before.sync_warnings[0]["excluded_exercise_ids"] == [fk023.CATALOG_ONLY_ID]
    assert before.legacy_mirror == MIRROR_SKIPPED_FK_TARGET_MISSING and before.mirrored_rows == 0

    dry = m.migrate(dry_run=True)
    planned = next(a.statements for a in dry.applied if a.filename == fk023.MIGRATION_FILENAME)
    assert len(planned) == 6
    assert fk023.mapping_fks(raw, "gym_equipment")[0]["ref_table"] == "exercise_zepp_mappings"

    calls_before = len(fake.calls_of("ExecuteStatement"))
    report = m.migrate()
    applied = next(a.statements for a in report.applied if a.filename == fk023.MIGRATION_FILENAME)
    assert applied == planned

    sent = [" ".join(c.params["sql"].split()) for c in fake.calls_of("ExecuteStatement")[calls_before:]]
    for statement in applied:
        assert sent.count(statement) == 1  # um statement por chamada, sem multi-statement

    for table, _, canonical_name in fk023.TABLES:
        assert [(fk["name"], fk["ref_table"], fk["delete_rule"]) for fk in fk023.mapping_fks(raw, table)] == [
            (canonical_name, "exercise_catalog", "CASCADE")
        ]
        assert fk023.column_type(raw, table) == "bigint unsigned"

    after = fk023.run_sync_and_link(_transaction, raw, data)
    assert after.sync_warnings == [] and after.link_warnings == []
    assert after.legacy_mirror == MIRROR_OK and after.mirrored_rows == 1
    assert (fk023.CATALOG_ONLY_ID, data.bench_id, 1) in after.gym_rows
    assert (fk023.CATALOG_ONLY_ID, data.cable_id, 1) in after.gym_rows

    # reexecucao: controle ja registra a 023, e a propria 023 nao planeja nada
    assert m.migrate().applied == []
    import app.db as db

    conn = db.get_conn()
    try:
        mdb = m.MigrationDB(conn, dry_run=True)
        m.load_python_migration(m.DEFAULT_MIGRATIONS_DIR / fk023.MIGRATION_FILENAME)(mdb)
        assert mdb.statements == []
        conn.commit()
    finally:
        conn.close()


def test_023_via_data_api_orfaos_falham_sem_ddl(fake, raw):
    data = fk023.seed(raw)
    with raw.cursor() as cur:
        cur.execute("INSERT INTO exercise_zepp_mappings (id, exercise_name, amazfit_name) VALUES (3001, 'Legado', 'Legado')")
        cur.execute(
            "INSERT INTO equipment_exercise_mappings (equipment_id, mapping_id) VALUES (%s, 3001)", (data.bench_id,)
        )
    calls_before = len(fake.calls_of("ExecuteStatement"))
    with pytest.raises(m.MigrationExecutionError, match=r"equipment_exercise_mappings\.mapping_id tem 1 linha"):
        m.migrate()
    sent = [c.params["sql"] for c in fake.calls_of("ExecuteStatement")[calls_before:]]
    assert not any("FOREIGN KEY" in sql and sql.lstrip().upper().startswith("ALTER") for sql in sent)
    for table, legacy_name, _ in fk023.TABLES:
        assert [fk["name"] for fk in fk023.mapping_fks(raw, table)] == [legacy_name]
