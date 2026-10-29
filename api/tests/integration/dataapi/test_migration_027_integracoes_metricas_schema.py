"""Migracao 027 (grupo B: devices, sleep_metrics, manual_effort_metrics,
intervals_activities, intervals_wellness, reports) nos dois backends - PyMySQL
e o caminho de PRODUCAO (shim `app/db_dataapi.py` + dublê da RDS Data API) -
num schema proprio (`<TEST_MYSQL_DATABASE sem _test>_grupob027_test`, 001..021
aplicadas: `devices` sem `user_id`, demais tabelas ausentes, como no baseline).

- banco sem os objetos: 2 ALTERs em `devices` + 5 `CREATE TABLE` (um statement
  por chamada), formato final IGUAL ao `information_schema` da Aurora de
  producao; reexecucao com 0 statements e nenhuma escrita;
- tabelas criadas pelos guards antigos completos (estado da Aurora e de dev):
  0 statements;
- versoes antigas/parciais dos guards, com linhas: so o que falta + backfill de
  `user_id` para o admin ativo (mesma regra dos guards);
- estado incompativel em qualquer tabela: falha antes do primeiro statement,
  nenhuma escrita, schema intacto (inclusive nas tabelas que precisariam de
  ALTER);
- runner `app.migrate` com 001..025 + 027 (sem 026), via Data API.
"""

from __future__ import annotations

import os
import shutil

import pytest

from app import migrate as m
from tests.support import grupo_b_schema as gb
from tests.support.dataapi_backend import BACKENDS, raw_mysql, use_dataapi
from tests.support.migrations import recreate_schema_with_migrations
from tests.support.sql_capture import ddl, recorded_sql, writes

FRESH_PLAN_PREFIXES = [
    "ALTER TABLE devices ADD COLUMN user_id BIGINT NULL AFTER id",
    "ALTER TABLE devices MODIFY COLUMN user_id BIGINT NOT NULL",
    "CREATE TABLE IF NOT EXISTS sleep_metrics (",
    "CREATE TABLE IF NOT EXISTS manual_effort_metrics (",
    "CREATE TABLE IF NOT EXISTS intervals_activities (",
    "CREATE TABLE IF NOT EXISTS intervals_wellness (",
    "CREATE TABLE IF NOT EXISTS reports (",
]


def _kwargs() -> dict:
    return dict(
        host=os.environ["MYSQL_HOST"],
        port=int(os.environ["MYSQL_PORT"]),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
    )


@pytest.fixture(scope="module")
def schema() -> str:
    base = os.environ["MYSQL_DATABASE"]
    database = f"{base[: -len('_test')]}_grupob027_test"
    recreate_schema_with_migrations(database=database, **_kwargs())
    return database


@pytest.fixture
def sraw(schema):
    with raw_mysql(database=schema, **_kwargs()) as conn:
        gb.reset_to_baseline(conn)
        yield conn
        with conn.cursor() as cur:  # nao contamina os proximos casos
            cur.execute("UPDATE users SET is_active = 1 WHERE role = 'admin'")


@pytest.fixture(params=BACKENDS)
def backend(request, monkeypatch, fake_rds, schema) -> str:
    if request.param == "data-api":
        use_dataapi(monkeypatch, fake_rds, schema)
    else:
        monkeypatch.setenv("MYSQL_DATABASE", schema)
    yield request.param
    if request.param == "data-api":
        assert fake_rds.open_transactions == [], "deixou transacao Data API aberta"


def _run(dry_run: bool = False) -> list[str]:
    import app.db as db  # atributo trocado por use_dataapi

    conn = db.get_conn()
    try:
        return gb.apply_027(conn, dry_run=dry_run)
    finally:
        conn.close()


def _admin(raw) -> int:
    with raw.cursor() as cur:
        cur.execute(
            "INSERT INTO users (username, password_hash, role, is_active) VALUES (%s, 'x', 'admin', 1)",
            (f"adm-{os.urandom(4).hex()}",),
        )
        cur.execute("SELECT id FROM users WHERE role = 'admin' AND is_active = 1 ORDER BY id ASC LIMIT 1")
        return cur.fetchone()["id"]


def _assert_prefixes(statements: list[str], prefixes: list[str]) -> None:
    assert len(statements) == len(prefixes), statements
    for statement, prefix in zip(statements, prefixes):
        assert statement.startswith(prefix), (statement, prefix)


def test_banco_sem_objetos_cria_formato_da_aurora_e_reexecucao_e_zero(backend, sraw, monkeypatch, fake_rds):
    before = gb.all_shapes(sraw)
    planned = _run(dry_run=True)
    _assert_prefixes(planned, FRESH_PLAN_PREFIXES)
    assert gb.all_shapes(sraw) == before  # dry-run nao altera nada

    with recorded_sql(backend, monkeypatch, fake_rds) as sent:
        applied = _run()
    assert applied == planned
    assert len(ddl(sent)) == len(FRESH_PLAN_PREFIXES)  # um statement por chamada
    gb.assert_aurora_shape(sraw)

    with recorded_sql(backend, monkeypatch, fake_rds) as sent:
        assert _run() == []
    assert writes(sent) == []


def test_tabelas_dos_guards_antigos_completos_e_zero_statements(backend, sraw, monkeypatch, fake_rds):
    gb.apply_legacy_guard_full(sraw)
    gb.assert_aurora_shape(sraw)  # guards antigos == Aurora == formato da 027

    with recorded_sql(backend, monkeypatch, fake_rds) as sent:
        assert _run() == []
    assert writes(sent) == []
    gb.assert_aurora_shape(sraw)


def _seed_legacy_rows(raw) -> None:
    with raw.cursor() as cur:
        cur.execute("INSERT INTO devices (device_name, token_hash) VALUES ('Relogio legado', %s)", (os.urandom(32).hex(),))
        cur.execute("INSERT INTO sleep_metrics (local_date, duration_hours) VALUES ('2026-09-01', 7.5), ('2026-09-02', 6.25)")
        cur.execute("INSERT INTO manual_effort_metrics (local_date, pai_score) VALUES ('2026-09-01', 80)")
        cur.execute(
            "INSERT INTO intervals_activities (external_id, activity_date) VALUES ('i1', '2026-09-01'), ('i2', '2026-09-02')"
        )
        cur.execute("INSERT INTO intervals_wellness (wellness_date, fitness_ctl) VALUES ('2026-09-01', 40), ('2026-09-02', 41)")


def test_versao_antiga_dos_guards_com_linhas_recebe_so_o_que_falta(backend, sraw, monkeypatch, fake_rds):
    admin_id = _admin(sraw)
    gb.run_sql(
        sraw,
        [
            gb.SLEEP_V1_SQL,
            gb.MANUAL_V1_SQL,
            gb.INTERVALS_ACTIVITIES_V1_SQL,
            gb.INTERVALS_WELLNESS_V1_SQL,
            gb.REPORTS_SQL.replace(",\n  KEY idx_reports_status (status)", ""),
        ],
    )
    _seed_legacy_rows(sraw)

    applied = _run()
    assert applied == [
        "ALTER TABLE devices ADD COLUMN user_id BIGINT NULL AFTER id",
        "UPDATE devices SET user_id = %s WHERE user_id IS NULL",
        "ALTER TABLE devices MODIFY COLUMN user_id BIGINT NOT NULL",
        "ALTER TABLE sleep_metrics ADD COLUMN user_id BIGINT NULL AFTER id",
        "UPDATE sleep_metrics SET user_id = %s WHERE user_id IS NULL",
        "ALTER TABLE sleep_metrics MODIFY COLUMN user_id BIGINT NOT NULL",
        "ALTER TABLE sleep_metrics DROP INDEX `local_date`",
        "ALTER TABLE sleep_metrics ADD UNIQUE KEY uq_sleep_metrics_user_date (user_id, local_date)",
        "ALTER TABLE manual_effort_metrics ADD COLUMN user_id BIGINT NULL AFTER id",
        "UPDATE manual_effort_metrics SET user_id = %s WHERE user_id IS NULL",
        "ALTER TABLE manual_effort_metrics MODIFY COLUMN user_id BIGINT NOT NULL",
        "ALTER TABLE manual_effort_metrics DROP INDEX `local_date`",
        "ALTER TABLE manual_effort_metrics ADD UNIQUE KEY uq_manual_effort_metrics_user_date (user_id, local_date)",
        "ALTER TABLE intervals_activities ADD COLUMN user_id BIGINT NULL AFTER id",
        "UPDATE intervals_activities SET user_id = %s WHERE user_id IS NULL",
        "ALTER TABLE intervals_activities MODIFY COLUMN user_id BIGINT NOT NULL",
        "ALTER TABLE intervals_activities DROP INDEX `uq_intervals_activities_external_id`",
        "ALTER TABLE intervals_activities ADD UNIQUE KEY uq_intervals_activities_user_external (user_id, external_id)",
        "ALTER TABLE intervals_wellness ADD COLUMN user_id BIGINT NULL AFTER wellness_date",
        "ALTER TABLE intervals_wellness ADD COLUMN readiness_score DECIMAL(6,2) NULL",
        "ALTER TABLE intervals_wellness ADD COLUMN daily_effort_load INT NULL",
        "UPDATE intervals_wellness SET user_id = %s WHERE user_id IS NULL",
        "ALTER TABLE intervals_wellness MODIFY COLUMN user_id BIGINT NOT NULL",
        "ALTER TABLE intervals_wellness DROP PRIMARY KEY, ADD PRIMARY KEY (user_id, wellness_date)",
        "ALTER TABLE reports ADD INDEX idx_reports_status (status)",
    ]
    gb.assert_aurora_shape(sraw)
    with sraw.cursor() as cur:
        for table in ("devices", "sleep_metrics", "manual_effort_metrics", "intervals_activities", "intervals_wellness"):
            cur.execute(f"SELECT DISTINCT user_id FROM {table}")
            assert [row["user_id"] for row in cur.fetchall()] == [admin_id], table
        cur.execute("SELECT COUNT(*) AS n FROM sleep_metrics")
        assert cur.fetchone()["n"] == 2  # nenhum dado perdido
    assert _run() == []


def test_estado_parte1_de_wellness_so_promove_user_id_e_troca_pk(backend, sraw, monkeypatch, fake_rds):
    admin_id = _admin(sraw)
    gb.apply_legacy_guard_full(sraw, tables=[t for t in gb.TABLES if t != "intervals_wellness"])
    gb.run_sql(
        sraw,
        [
            gb.INTERVALS_WELLNESS_GUARD_SQL,
            "ALTER TABLE intervals_wellness ADD COLUMN readiness_score DECIMAL(6,2) NULL",
            "ALTER TABLE intervals_wellness ADD COLUMN daily_effort_load INT NULL",
        ],
    )
    with sraw.cursor() as cur:
        cur.execute("INSERT INTO intervals_wellness (wellness_date, user_id) VALUES ('2026-09-01', %s)", (admin_id,))

    assert _run() == [
        "ALTER TABLE intervals_wellness MODIFY COLUMN user_id BIGINT NOT NULL",
        "ALTER TABLE intervals_wellness DROP PRIMARY KEY, ADD PRIMARY KEY (user_id, wellness_date)",
    ]
    gb.assert_aurora_shape(sraw)
    assert _run() == []


# ---------------------------------------------------------------------------
# Estados incompativeis. Base: `devices` no baseline (a 027 PRECISARIA de ALTER
# nela, que vem antes na ordem) + guards completos nas demais - prova que a
# falha acontece antes de qualquer statement de qualquer tabela.
# ---------------------------------------------------------------------------


def _base(raw, skip=()):
    gb.apply_legacy_guard_full(raw, tables=[t for t in gb.NON_BASELINE_TABLES if t not in skip])


def _sleep_decimal(raw):
    _base(raw)
    gb.run_sql(raw, ["ALTER TABLE sleep_metrics MODIFY COLUMN duration_hours DECIMAL(5,2) NULL"])


def _reports_status_varchar(raw):
    _base(raw)
    gb.run_sql(raw, ["ALTER TABLE reports MODIFY COLUMN status VARCHAR(20) NOT NULL DEFAULT 'generating'"])


def _reports_user_nullable(raw):
    _base(raw)
    gb.run_sql(raw, ["ALTER TABLE reports MODIFY COLUMN user_id BIGINT NULL"])


def _reports_myisam(raw):
    _base(raw)
    gb.run_sql(raw, ["ALTER TABLE reports ENGINE=MyISAM"])


def _reports_sem_coluna(raw):
    _base(raw)
    gb.run_sql(raw, ["ALTER TABLE reports DROP COLUMN pdf_path"])


def _activities_user_int(raw):
    _base(raw, skip=("intervals_activities",))
    gb.run_sql(
        raw,
        [gb.INTERVALS_ACTIVITIES_V1_SQL, "ALTER TABLE intervals_activities ADD COLUMN user_id INT NULL AFTER id"],
    )


def _sleep_unique_em_outras_colunas(raw):
    _base(raw)
    gb.run_sql(
        raw,
        [
            "ALTER TABLE sleep_metrics DROP INDEX uq_sleep_metrics_user_date",
            "ALTER TABLE sleep_metrics ADD UNIQUE KEY uq_sleep_metrics_user_date (local_date, user_id)",
        ],
    )


def _wellness_pk_outra(raw):
    _base(raw)
    gb.run_sql(
        raw, ["ALTER TABLE intervals_wellness DROP PRIMARY KEY, ADD PRIMARY KEY (user_id, wellness_date, synced_at)"]
    )


def _null_user_sem_admin(raw):
    _base(raw, skip=("sleep_metrics",))
    gb.run_sql(raw, [gb.SLEEP_V1_SQL])
    with raw.cursor() as cur:
        cur.execute("UPDATE users SET is_active = 0 WHERE role = 'admin'")
        cur.execute("INSERT INTO sleep_metrics (local_date) VALUES ('2026-09-01')")


def _colisao_na_chave_composta(raw):
    admin_id = _admin(raw)
    _base(raw, skip=("manual_effort_metrics",))
    gb.run_sql(
        raw,
        [
            gb.MANUAL_V1_SQL,
            "ALTER TABLE manual_effort_metrics ADD COLUMN user_id BIGINT NULL AFTER id",
            "ALTER TABLE manual_effort_metrics DROP INDEX local_date",
        ],
    )
    with raw.cursor() as cur:
        cur.execute(
            "INSERT INTO manual_effort_metrics (user_id, local_date) VALUES (%s, '2026-09-01'), (NULL, '2026-09-01')",
            (admin_id,),
        )


INCOMPATIBLE = {
    "sleep-duration-decimal-5-2": (_sleep_decimal, r"sleep_metrics\.duration_hours e decimal\(5,2\)"),
    "reports-status-varchar": (_reports_status_varchar, r"reports\.status e varchar\(20\)"),
    "reports-user_id-nullable": (_reports_user_nullable, r"reports\.user_id tem IS_NULLABLE=YES"),
    "reports-myisam": (_reports_myisam, r"reports usa ENGINE=MyISAM"),
    "reports-sem-pdf_path": (_reports_sem_coluna, r"reports\.pdf_path nao existe"),
    "activities-user_id-int": (_activities_user_int, r"intervals_activities\.user_id e int"),
    "sleep-unique-em-outras-colunas": (
        _sleep_unique_em_outras_colunas,
        r"indice uq_sleep_metrics_user_date de sleep_metrics existe em \['local_date', 'user_id'\]",
    ),
    "wellness-pk-diferente": (
        _wellness_pk_outra,
        r"intervals_wellness tem PRIMARY KEY \['user_id', 'wellness_date', 'synced_at'\]",
    ),
    "user_id-null-sem-admin": (_null_user_sem_admin, r"1 linha\(s\) de sleep_metrics com user_id NULL e nenhum admin ativo"),
    "colisao-na-chave-composta": (
        _colisao_na_chave_composta,
        r"1 grupo\(s\) duplicado\(s\) em manual_effort_metrics \['user_id', 'local_date'\]",
    ),
}


@pytest.mark.parametrize("case", sorted(INCOMPATIBLE))
def test_estado_incompativel_falha_sem_aplicar_nada(backend, sraw, monkeypatch, fake_rds, case):
    arrange, message = INCOMPATIBLE[case]
    arrange(sraw)
    before = gb.all_shapes(sraw)
    assert "user_id" not in [c[1] for c in before["devices"]["columns"]]  # devices precisaria de ALTER

    with recorded_sql(backend, monkeypatch, fake_rds) as sent:
        with pytest.raises(Exception, match=message) as exc_info:
            _run()
    assert "Nenhum statement executado" in str(exc_info.value)
    assert type(exc_info.value).__name__ == "IntegracoesMetricasSchemaError"
    assert writes(sent) == []
    assert gb.all_shapes(sraw) == before


@pytest.mark.parametrize("com_026", [False, True], ids=["001-025+027", "001-027"])
def test_runner_aplica_027_com_e_sem_026(sraw, monkeypatch, fake_rds, schema, tmp_path, com_026):
    """Pelo runner (`app.migrate`), caminho Data API: a 027 funciona logo apos a
    025 (diretorio sem a 026) e depois da 026 (diretorio completo do repo)."""
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    for path in m.DEFAULT_MIGRATIONS_DIR.iterdir():
        # Ate a 027: migracoes posteriores (028+) tem testes proprios.
        match = m.MIGRATION_FILENAME_RE.match(path.name)
        if match and int(match.group("version")) <= 27 and (com_026 or not path.name.startswith("026_")):
            shutil.copy2(path, migrations_dir / path.name)
    expected = sorted(
        p.name for p in migrations_dir.iterdir() if int(p.name[:3]) > m.BASELINE_MAX_VERSION
    )
    assert expected[-1] == gb.MIGRATION_FILENAME
    assert any(n.startswith("026_") for n in expected) is com_026

    with sraw.cursor() as cur:
        for table in (m.CONTROL_TABLE, m.LOCK_TABLE):
            cur.execute(f"DROP TABLE IF EXISTS `{table}`")
    use_dataapi(monkeypatch, fake_rds, schema)

    report = m.migrate(migrations_dir=migrations_dir)
    applied = {a.filename: a.statements for a in report.applied}
    assert list(applied) == expected
    _assert_prefixes(applied[gb.MIGRATION_FILENAME], FRESH_PLAN_PREFIXES)
    gb.assert_aurora_shape(sraw)
    assert m.migrate(migrations_dir=migrations_dir).applied == []
    assert fake_rds.open_transactions == []
