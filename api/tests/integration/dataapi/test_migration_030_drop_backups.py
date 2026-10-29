"""Migracao 030 (DROP das tabelas de backup do onprem) nos dois backends -
PyMySQL e o caminho de PRODUCAO (shim `app/db_dataapi.py` + dublê da RDS Data
API) - num schema proprio (`<TEST_MYSQL_DATABASE sem _test>_p030_test`) no
formato de um banco novo de CI: 001..021 + runner com 022..NNN (inclui a 030).

- banco novo (CI/dev): o runner aplica ate a 030 e a 030 tem 0 statements;
- banco com as duas tabelas no formato REAL da Aurora (colunas, indices,
  collation, com linhas): 2 DROP, um statement por chamada; reexecucao 0;
- FK entrando/saindo ou view referenciando: falha antes do primeiro
  statement, 0 escritas, tabelas intactas;
- runner com as tabelas presentes: 030 aplicada com os 2 statements;
- lacuna de numeracao: uma 029 que chega DEPOIS da 030 aplicada e aplicada
  normalmente no run seguinte (sem erro de ordem/checksum).
"""

from __future__ import annotations

import os
import shutil

import pytest

from app import migrate as m
from app.db_pymysql import Connection as PyMySQLConnection
from tests.support import drop_backups_030 as d
from tests.support.dataapi_backend import BACKENDS, raw_mysql, use_dataapi
from tests.support.migrations import recreate_schema_with_migrations
from tests.support.sql_capture import ddl, recorded_sql, writes

PROBE_TABLES = ("p030_fk_filha", "p030_pai")
PROBE_VIEW = "p030_view"


def _kwargs() -> dict:
    return dict(
        host=os.environ["MYSQL_HOST"],
        port=int(os.environ["MYSQL_PORT"]),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
    )


@pytest.fixture(scope="module")
def schema() -> dict:
    base = os.environ["MYSQL_DATABASE"]
    database = f"{base[: -len('_test')]}_p030_test"
    recreate_schema_with_migrations(database=database, **_kwargs())
    kw = _kwargs()
    report = m.migrate(
        conn_factory=lambda: PyMySQLConnection(kw["host"], kw["port"], kw["user"], kw["password"], database),
        migrations_dir=m.DEFAULT_MIGRATIONS_DIR,
    )
    return {"database": database, "report": report}


def _cleanup(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(f"DROP VIEW IF EXISTS `{PROBE_VIEW}`")
        cur.execute("SET FOREIGN_KEY_CHECKS = 0")
        try:
            for table in (*PROBE_TABLES, *d.BACKUP_TABLES):
                cur.execute(f"DROP TABLE IF EXISTS `{table}`")
        finally:
            cur.execute("SET FOREIGN_KEY_CHECKS = 1")
    conn.commit()


@pytest.fixture
def sraw(schema):
    with raw_mysql(database=schema["database"], **_kwargs()) as conn:
        _cleanup(conn)
        yield conn
        _cleanup(conn)


@pytest.fixture(params=BACKENDS)
def backend(request, monkeypatch, fake_rds, schema) -> str:
    if request.param == "data-api":
        use_dataapi(monkeypatch, fake_rds, schema["database"])
    else:
        monkeypatch.setenv("MYSQL_DATABASE", schema["database"])
    monkeypatch.delenv("MIGRATIONS_DIR", raising=False)
    yield request.param
    if request.param == "data-api":
        assert fake_rds.open_transactions == [], "deixou transacao Data API aberta"


def _run(dry_run: bool = False) -> list[str]:
    import app.db as db  # atributo trocado por use_dataapi

    conn = db.get_conn()
    try:
        return d.apply_030(conn, dry_run=dry_run)
    finally:
        conn.close()


def _information_schema_reads(sent: list[str]) -> list[str]:
    return [s for s in sent if "information_schema" in s.lower()]


# ---------------------------------------------------------------------------


def test_runner_em_banco_novo_aplica_ate_a_030_sem_statements_na_030(schema):
    report = schema["report"]
    real = sorted(p.name for p in m.DEFAULT_MIGRATIONS_DIR.iterdir() if m.MIGRATION_FILENAME_RE.match(p.name))
    pending = [name for name in real if int(name[:3]) > m.BASELINE_MAX_VERSION]
    assert [a.filename for a in report.applied] == pending  # 022..NNN (inclui a 029 se ja existir)
    assert d.MIGRATION_FILENAME in pending
    applied_030 = next(a for a in report.applied if a.filename == d.MIGRATION_FILENAME)
    assert applied_030.statements == []  # dev/CI: tabelas de backup nunca existiram


def test_banco_com_as_duas_tabelas_no_formato_real_executa_2_drops(backend, sraw, monkeypatch, fake_rds):
    d.create_backup_tables(sraw)
    assert d.real_format(sraw) == d.snapshot_format(d.load_snapshot())  # formato identico ao da Aurora

    assert _run(dry_run=True) == d.AURORA_PLAN
    assert d.existing_backup_tables(sraw) == sorted(d.BACKUP_TABLES)  # dry-run nao altera nada

    with recorded_sql(backend, monkeypatch, fake_rds) as sent:
        assert _run() == d.AURORA_PLAN
    assert ddl(sent) == d.AURORA_PLAN  # um statement por chamada, na ordem
    assert writes(sent) == d.AURORA_PLAN
    assert d.existing_backup_tables(sraw) == []

    with recorded_sql(backend, monkeypatch, fake_rds) as sent:
        assert _run() == []
    assert writes(sent) == []


def test_banco_sem_as_tabelas_0_statements_e_uma_leitura(backend, sraw, monkeypatch, fake_rds):
    with recorded_sql(backend, monkeypatch, fake_rds) as sent:
        assert _run() == []
    assert writes(sent) == []
    assert len(_information_schema_reads(sent)) == 1


def test_retomada_com_uma_tabela_so_remove_a_restante(backend, sraw):
    d.create_backup_tables(sraw, tables=(d.BACKUP_TABLES[1],))
    assert _run() == [f"DROP TABLE {d.BACKUP_TABLES[1]}"]
    assert d.existing_backup_tables(sraw) == []


def _fk_entrando(raw) -> str:
    with raw.cursor() as cur:
        cur.execute(
            "CREATE TABLE p030_fk_filha (id BIGINT PRIMARY KEY, backup_id BIGINT NULL, "
            "CONSTRAINT fk_p030_entrando FOREIGN KEY (backup_id) REFERENCES exercises_backup_before_mapping_sync(id))"
        )
    return "fk_p030_entrando"


def _fk_saindo(raw) -> str:
    with raw.cursor() as cur:
        cur.execute("CREATE TABLE p030_pai (id BIGINT PRIMARY KEY)")
        cur.execute("SET FOREIGN_KEY_CHECKS = 0")
        try:
            cur.execute(
                "ALTER TABLE exercise_mappings_backup_20260714 ADD CONSTRAINT fk_p030_saindo "
                "FOREIGN KEY (id) REFERENCES p030_pai(id)"
            )
        finally:
            cur.execute("SET FOREIGN_KEY_CHECKS = 1")
    return "fk_p030_saindo"


def _view(raw) -> str:
    with raw.cursor() as cur:
        cur.execute(f"CREATE VIEW {PROBE_VIEW} AS SELECT id, exercise_name FROM exercise_mappings_backup_20260714")
    return PROBE_VIEW


@pytest.mark.parametrize("make_blocker", [_fk_entrando, _fk_saindo, _view], ids=["fk-entrando", "fk-saindo", "view"])
def test_fk_ou_view_envolvendo_backup_falha_sem_nenhuma_escrita(backend, sraw, monkeypatch, fake_rds, make_blocker):
    d.create_backup_tables(sraw)
    blocker = make_blocker(sraw)
    sraw.commit()

    with recorded_sql(backend, monkeypatch, fake_rds) as sent:
        with pytest.raises(Exception, match=blocker) as exc:
            _run()
    assert "Nenhum statement executado" in str(exc.value)
    assert writes(sent) == []
    assert d.existing_backup_tables(sraw) == sorted(d.BACKUP_TABLES)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def _delete_records(raw, where: str, params) -> list[dict]:
    with raw.cursor() as cur:
        cur.execute(f"SELECT filename, version, kind, checksum, applied_at, execution_ms FROM {m.CONTROL_TABLE} WHERE {where}", params)
        rows = list(cur.fetchall())
        cur.execute(f"DELETE FROM {m.CONTROL_TABLE} WHERE {where}", params)
    raw.commit()
    return rows


def _restore_records(raw, rows: list[dict]) -> None:
    with raw.cursor() as cur:
        for r in rows:
            cur.execute(
                f"INSERT IGNORE INTO {m.CONTROL_TABLE} (filename, version, kind, checksum, applied_at, execution_ms) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (r["filename"], r["version"], r["kind"], r["checksum"], r["applied_at"], r["execution_ms"]),
            )
    raw.commit()


def test_runner_com_as_tabelas_presentes_aplica_a_030_com_os_2_drops(backend, sraw, tmp_path):
    migrations = tmp_path / "migrations"
    shutil.copytree(m.DEFAULT_MIGRATIONS_DIR, migrations, ignore=shutil.ignore_patterns("__pycache__"))
    _delete_records(sraw, "filename = %s", (d.MIGRATION_FILENAME,))
    d.create_backup_tables(sraw)

    report = m.migrate(migrations_dir=migrations)
    assert [(a.filename, a.statements) for a in report.applied] == [(d.MIGRATION_FILENAME, d.AURORA_PLAN)]
    assert d.existing_backup_tables(sraw) == []

    again = m.migrate(migrations_dir=migrations)
    assert again.applied == []


def test_lacuna_029_chegando_depois_da_030_e_aplicada_sem_erro(backend, sraw, tmp_path):
    migrations = tmp_path / "migrations"
    shutil.copytree(
        m.DEFAULT_MIGRATIONS_DIR, migrations, ignore=shutil.ignore_patterns("__pycache__", "029_*")
    )
    saved_029 = _delete_records(sraw, "version = %s", (29,))
    try:
        # 030 ja aplicada e nenhuma 029 no diretorio: nada a fazer.
        assert m.migrate(migrations_dir=migrations).applied == []

        (migrations / "029_lacuna_probe.py").write_text("def up(db):\n    db.query('SELECT 1 AS n')\n", encoding="utf-8")
        report = m.migrate(migrations_dir=migrations)
        assert [a.filename for a in report.applied] == ["029_lacuna_probe.py"]
        states = {row["filename"]: row["state"] for row in m.status(migrations_dir=migrations)}
        assert states["029_lacuna_probe.py"] == "applied" and states[d.MIGRATION_FILENAME] == "applied"
        assert m.migrate(migrations_dir=migrations).applied == []
    finally:
        _delete_records(sraw, "filename = %s", ("029_lacuna_probe.py",))
        _restore_records(sraw, saved_029)
