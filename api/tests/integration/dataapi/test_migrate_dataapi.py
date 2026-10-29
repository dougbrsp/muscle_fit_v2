"""`app/migrate.py` fim a fim no caminho de producao (shim Data API + dublê),
num schema proprio (`<TEST_MYSQL_DATABASE sem _test>_migrate_test`) com as
migrations 001..021 aplicadas (como Aurora/local ja estao).

Cobre o caminho de ESCRITA que o dry-run nao cobre: criacao das tabelas de
controle dentro da transacao aberta no construtor da `Connection` do shim,
lock com lease (`UTC_TIMESTAMP() + INTERVAL %s SECOND`), baseline <= 021,
aplicacao de `022_equipment_types_parity.py`, idempotencia, lock ocupado/
expirado, falha no meio, checksum, migracao `.sql` multi-statement, handler
da Lambda e retomada de auto-pause.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timedelta

import pytest

from app import db_dataapi, migrate as m
from tests.support.dataapi_backend import raw_mysql, use_dataapi
from tests.support.fk023 import restore_legacy_mapping_fks
from tests.support.migrations import recreate_schema_with_migrations, undo_reshaping_migrations

REAL_MIGRATIONS = sorted(p.name for p in m.DEFAULT_MIGRATIONS_DIR.iterdir() if m.MIGRATION_FILENAME_RE.match(p.name))
BASELINE = [name for name in REAL_MIGRATIONS if int(name[:3]) <= m.BASELINE_MAX_VERSION]
PENDING = [name for name in REAL_MIGRATIONS if int(name[:3]) > m.BASELINE_MAX_VERSION]
PROBE_TABLES = ("migrate_probe_parcial", "migrate_probe_sql", "migrate_probe_py")


def _kwargs():
    return dict(
        host=os.environ["MYSQL_HOST"],
        port=int(os.environ["MYSQL_PORT"]),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
    )


@pytest.fixture(scope="module")
def migrate_schema() -> str:
    base = os.environ["MYSQL_DATABASE"]
    database = f"{base[: -len('_test')]}_migrate_test"
    recreate_schema_with_migrations(database=database, **_kwargs())
    return database


@pytest.fixture
def mraw(migrate_schema):
    with raw_mysql(database=migrate_schema, **_kwargs()) as conn:
        yield conn


def _scalar(conn, sql, params=()):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        return None if row is None else next(iter(row.values()))


def _table_exists(conn, table):
    return _scalar(
        conn,
        "SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
        (table,),
    ) > 0


def _reset(conn):
    """Volta o schema ao estado 'so 001..021 aplicadas, sem controle'."""
    # sem a tabela de controle o runner reexecuta tudo: volta a forma anterior
    # das migracoes que mudam o que uma anterior confere (ex.: 068 x 036)
    undo_reshaping_migrations(conn)
    with conn.cursor() as cur:
        for table in (m.CONTROL_TABLE, m.LOCK_TABLE, *PROBE_TABLES):
            cur.execute(f"DROP TABLE IF EXISTS `{table}`")
        if _scalar(conn, "SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'equipment' AND CONSTRAINT_NAME = 'fk_equipment_type'"):
            cur.execute("ALTER TABLE equipment DROP FOREIGN KEY fk_equipment_type")
        if _scalar(conn, "SELECT COUNT(*) FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'equipment' AND INDEX_NAME = 'idx_equipment_type'"):
            cur.execute("ALTER TABLE equipment DROP INDEX idx_equipment_type")
        for column in ("equipment_type_id", "manufacturer", "model", "location"):
            if _scalar(conn, "SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'equipment' AND COLUMN_NAME = %s", (column,)):
                cur.execute(f"ALTER TABLE equipment DROP COLUMN {column}")
        cur.execute("DROP TABLE IF EXISTS equipment_types")
    # 023: FKs de mapping_id de volta para exercise_zepp_mappings (BIGINT signed).
    restore_legacy_mapping_fks(conn)


@pytest.fixture
def fake(monkeypatch, fake_rds, migrate_schema, mraw):
    _reset(mraw)
    use_dataapi(monkeypatch, fake_rds, migrate_schema)
    monkeypatch.delenv("MIGRATIONS_DIR", raising=False)
    yield fake_rds
    assert fake_rds.open_transactions == [], "migrate deixou transacao Data API aberta"


@pytest.fixture
def tmp_migrations(tmp_path):
    directory = tmp_path / "migrations"
    directory.mkdir()
    for name in REAL_MIGRATIONS:
        shutil.copy2(m.DEFAULT_MIGRATIONS_DIR / name, directory / name)
    return directory


def _executed_sql(fake):
    return [" ".join(c.params["sql"].split()) for c in fake.calls_of("ExecuteStatement")]


def _records(conn):
    with conn.cursor() as cur:
        cur.execute(f"SELECT filename, version, kind, checksum, applied_at, execution_ms FROM {m.CONTROL_TABLE} ORDER BY version")
        return cur.fetchall()


# ---------------------------------------------------------------------------


def test_pre_condicao_real_tem_baseline_ate_021_e_022_pendente():
    assert len(BASELINE) == 21
    assert "022_equipment_types_parity.py" in PENDING


def test_status_e_somente_leitura(fake, mraw):
    rows = m.status()
    assert [r["state"] for r in rows if r["filename"] in BASELINE] == ["pending-baseline"] * len(BASELINE)
    assert [r["state"] for r in rows if r["filename"] in PENDING] == ["pending"] * len(PENDING)
    assert not _table_exists(mraw, m.CONTROL_TABLE)
    assert all(sql.startswith("SELECT") for sql in _executed_sql(fake))


def test_dry_run_planeja_sem_escrever(fake, mraw):
    report = m.migrate(dry_run=True)
    assert report.dry_run and report.lock_owner is None
    assert report.baselined == BASELINE
    assert [a.filename for a in report.applied] == PENDING
    planned = report.applied[0].statements
    assert planned[0].startswith("CREATE TABLE IF NOT EXISTS equipment_types")
    assert "ALTER TABLE equipment ADD COLUMN equipment_type_id BIGINT UNSIGNED NULL" in planned
    assert planned[-1].startswith("ALTER TABLE equipment ADD CONSTRAINT fk_equipment_type")
    assert all(sql.startswith("SELECT") for sql in _executed_sql(fake))
    assert not _table_exists(mraw, m.CONTROL_TABLE)
    assert not _table_exists(mraw, "equipment_types")
    json.dumps(report.to_dict())


def test_migrate_fim_a_fim_e_idempotente(fake, mraw):
    before = datetime.utcnow().replace(microsecond=0) - timedelta(seconds=5)
    report = m.migrate()

    assert report.status == "ok" and not report.dry_run
    assert report.baselined == BASELINE
    assert [a.filename for a in report.applied] == PENDING
    assert report.already_applied == 0
    assert report.lock_owner

    records = _records(mraw)
    assert [r["filename"] for r in records] == BASELINE + PENDING
    checksums = {mig.filename: mig.checksum for mig in m.discover_migrations()}
    for record in records:
        assert record["checksum"] == checksums[record["filename"]]
        assert record["applied_at"] >= before
        if record["filename"] in BASELINE:
            assert (record["kind"], record["execution_ms"]) == ("baseline", None)
        else:
            assert record["kind"] == "applied" and record["execution_ms"] >= 0
    assert _scalar(mraw, f"SELECT COUNT(*) FROM {m.LOCK_TABLE}") == 0

    # 022 aplicada de verdade
    assert _table_exists(mraw, "equipment_types")
    assert _scalar(mraw, "SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'equipment' AND COLUMN_NAME IN ('equipment_type_id','manufacturer','model','location')") == 4
    assert _scalar(mraw, "SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'equipment' AND CONSTRAINT_NAME = 'fk_equipment_type'") == 1

    # DDL de controle rodou DENTRO da transacao aberta no construtor do shim;
    # depois do primeiro commit tudo segue em autocommit (sem transactionId).
    executes = fake.calls_of("ExecuteStatement")
    create_control = next(c for c in executes if f"CREATE TABLE IF NOT EXISTS {m.CONTROL_TABLE}" in c.params["sql"])
    create_lock = next(c for c in executes if f"CREATE TABLE IF NOT EXISTS {m.LOCK_TABLE}" in c.params["sql"])
    assert create_control.params.get("transactionId")
    assert create_lock.params.get("transactionId") == create_control.params["transactionId"]
    lock_insert = next(c for c in executes if c.params["sql"].startswith(f"INSERT IGNORE INTO {m.LOCK_TABLE}"))
    assert "transactionId" not in lock_insert.params
    assert [p.get("typeHint") for p in lock_insert.params["parameters"]] == [None, None, None]
    assert [c.operation for c in fake.calls].count("CommitTransaction") == 1

    assert {r["state"] for r in m.status()} == {"baseline", "applied"}

    # 2a execucao: nada a fazer
    second = m.migrate()
    assert (second.baselined, second.applied, second.already_applied) == ([], [], len(REAL_MIGRATIONS))
    assert len(_records(mraw)) == len(REAL_MIGRATIONS)
    assert _scalar(mraw, f"SELECT COUNT(*) FROM {m.LOCK_TABLE}") == 0


def _write_migration(directory, name, body):
    (directory / name).write_text(body, encoding="utf-8")


def test_lock_ocupado_bloqueia_sem_aplicar_nada(fake, mraw, tmp_migrations):
    m.migrate(migrations_dir=tmp_migrations)
    _write_migration(tmp_migrations, "900_probe.py", "def up(db):\n    db.execute('CREATE TABLE migrate_probe_py (id INT)')\n")
    with mraw.cursor() as cur:
        cur.execute(
            f"INSERT INTO {m.LOCK_TABLE} (lock_name, owner, acquired_at, expires_at) "
            "VALUES (%s, 'outra-execucao', UTC_TIMESTAMP(), UTC_TIMESTAMP() + INTERVAL 600 SECOND)",
            (m.LOCK_NAME,),
        )
    with pytest.raises(m.MigrationLockError, match="outra-execucao"):
        m.migrate(migrations_dir=tmp_migrations)
    assert "900_probe.py" not in {r["filename"] for r in _records(mraw)}
    assert not _table_exists(mraw, "migrate_probe_py")
    assert _scalar(mraw, f"SELECT owner FROM {m.LOCK_TABLE}") == "outra-execucao"


def test_lock_expirado_e_tomado(fake, mraw, tmp_migrations):
    m.migrate(migrations_dir=tmp_migrations)
    _write_migration(tmp_migrations, "900_probe.py", "def up(db):\n    db.execute('CREATE TABLE migrate_probe_py (id INT)')\n")
    with mraw.cursor() as cur:
        cur.execute(
            f"INSERT INTO {m.LOCK_TABLE} (lock_name, owner, acquired_at, expires_at) "
            "VALUES (%s, 'processo-morto', UTC_TIMESTAMP() - INTERVAL 3600 SECOND, UTC_TIMESTAMP() - INTERVAL 60 SECOND)",
            (m.LOCK_NAME,),
        )
    report = m.migrate(migrations_dir=tmp_migrations)
    assert [a.filename for a in report.applied] == ["900_probe.py"]
    assert _table_exists(mraw, "migrate_probe_py")
    assert _scalar(mraw, f"SELECT COUNT(*) FROM {m.LOCK_TABLE}") == 0


def test_falha_no_meio_nao_registra_libera_lock_e_permite_reexecutar(fake, mraw, tmp_migrations):
    m.migrate(migrations_dir=tmp_migrations)
    _write_migration(
        tmp_migrations,
        "900_falha.py",
        "def up(db):\n"
        "    db.execute('CREATE TABLE IF NOT EXISTS migrate_probe_parcial (id INT PRIMARY KEY)')\n"
        "    db.execute('INSERT INTO tabela_que_nao_existe VALUES (1)')\n",
    )
    with pytest.raises(m.MigrationExecutionError, match=r"900_falha\.py: .*tabela_que_nao_existe"):
        m.migrate(migrations_dir=tmp_migrations)
    assert "900_falha.py" not in {r["filename"] for r in _records(mraw)}
    assert _scalar(mraw, f"SELECT COUNT(*) FROM {m.LOCK_TABLE}") == 0
    assert _table_exists(mraw, "migrate_probe_parcial")  # DDL nao e atomico: fica o parcial

    _write_migration(
        tmp_migrations,
        "900_falha.py",
        "def up(db):\n"
        "    db.execute('CREATE TABLE IF NOT EXISTS migrate_probe_parcial (id INT PRIMARY KEY)')\n"
        "    db.execute('INSERT IGNORE INTO migrate_probe_parcial VALUES (1)')\n",
    )
    report = m.migrate(migrations_dir=tmp_migrations)
    assert [a.filename for a in report.applied] == ["900_falha.py"]
    assert _scalar(mraw, "SELECT COUNT(*) FROM migrate_probe_parcial") == 1


def test_checksum_alterado_apos_aplicar_falha_antes_de_executar(fake, mraw, tmp_migrations):
    _write_migration(tmp_migrations, "900_probe.py", "def up(db):\n    db.execute('CREATE TABLE migrate_probe_py (id INT)')\n")
    m.migrate(migrations_dir=tmp_migrations)
    _write_migration(tmp_migrations, "900_probe.py", "def up(db):\n    db.execute('DROP TABLE migrate_probe_py')\n")
    with pytest.raises(m.MigrationChecksumError, match="900_probe.py"):
        m.migrate(migrations_dir=tmp_migrations)
    assert _table_exists(mraw, "migrate_probe_py")
    assert _scalar(mraw, f"SELECT COUNT(*) FROM {m.LOCK_TABLE}") == 0


def test_migracao_sql_com_varios_statements_vira_uma_chamada_por_statement(fake, mraw, tmp_migrations):
    """A Data API rejeita multi-statement: o split do runner precisa mandar um por chamada."""
    m.migrate(migrations_dir=tmp_migrations)
    _write_migration(
        tmp_migrations,
        "900_probe.sql",
        "-- comentario; com ponto e virgula\n"
        "CREATE TABLE IF NOT EXISTS migrate_probe_sql (id INT PRIMARY KEY, txt VARCHAR(20));\n"
        "INSERT IGNORE INTO migrate_probe_sql VALUES (1, 'a;b'), (2, 'c''d');\n",
    )
    calls_before = len(fake.calls_of("ExecuteStatement"))
    report = m.migrate(migrations_dir=tmp_migrations)
    assert [a.filename for a in report.applied] == ["900_probe.sql"]
    assert len(report.applied[0].statements) == 2
    with mraw.cursor() as cur:
        cur.execute("SELECT id, txt FROM migrate_probe_sql ORDER BY id")
        assert cur.fetchall() == [{"id": 1, "txt": "a;b"}, {"id": 2, "txt": "c'd"}]
    assert len(fake.calls_of("ExecuteStatement")) > calls_before


def test_handler_da_lambda_no_backend_data_api(fake, mraw, monkeypatch):
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "musclefit-migrations")
    monkeypatch.setenv("AWS_LAMBDA_LOG_STREAM_NAME", "2026/09/13/[$LATEST]abc")
    status = m.handler({"action": "status"}, None)
    assert status["status"] == "ok" and len(status["migrations"]) == len(REAL_MIGRATIONS)
    dry = m.handler({"action": "dry-run"}, None)
    assert dry["dry_run"] is True and not _table_exists(mraw, m.CONTROL_TABLE)
    applied = m.handler({}, None)
    assert [a["filename"] for a in applied["applied"]] == PENDING
    assert applied["lock_owner"].startswith("2026/09/13/[$LATEST]abc:")


def test_handler_recusa_backend_pymysql_dentro_da_lambda(fake, monkeypatch):
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "musclefit-migrations")
    monkeypatch.setenv("DB_BACKEND", "pymysql")
    with pytest.raises(m.MigrationError, match="DB_BACKEND"):
        m.handler({"action": "status"}, None)
    assert fake.calls == []


def test_migrate_espera_aurora_retomar_do_auto_pause(fake, mraw, monkeypatch):
    sleeps = []
    monkeypatch.setattr(m.time, "sleep", sleeps.append)
    # `m.time` e o modulo `time`: o patch acima troca `time.sleep` GLOBALMENTE,
    # inclusive o do retry de retomada do shim (`db_dataapi._sleep`). Sem
    # desligar a camada de baixo, ela absorveria as falhas injetadas e este
    # teste passaria medindo o retry ERRADO. Tupla vazia = sem retry no shim.
    monkeypatch.setattr(db_dataapi, "_RESUME_RETRY_WAITS", ())
    fake.inject_error(
        "BeginTransaction",
        "DatabaseResumingException",
        "The Aurora DB instance db-XYZ is resuming after being auto-paused. Please wait a few seconds and try again.",
        times=2,
    )
    report = m.migrate()
    assert len(sleeps) == 2
    assert [a.filename for a in report.applied] == PENDING


def test_cli_status_json(fake, capsys):
    assert m.main(["--status", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == len(REAL_MIGRATIONS)
