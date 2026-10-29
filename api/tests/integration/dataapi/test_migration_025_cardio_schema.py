"""Migracao 025 (`cardio_recommendations`) nos dois backends - PyMySQL e o
caminho de PRODUCAO (shim `app/db_dataapi.py` + dublê da RDS Data API) - num
schema proprio (`<TEST_MYSQL_DATABASE sem _test>_cardio025_test`, 001..021
aplicadas, onde a tabela NAO existe, como no baseline).

- banco sem a tabela: 1 `CREATE TABLE` (uma chamada), formato final igual ao
  medido em dev; reexecucao com 0 statements;
- tabela criada pelo guard antigo completo (estado esperado na Aurora e em
  dev): 0 statements e nenhuma escrita;
- tabela de uma versao antiga do guard: so os ALTERs que faltam + backfill de
  `user_id` para o admin ativo (mesma regra do guard);
- estado incompativel: falha antes do primeiro statement, schema intacto.
"""

from __future__ import annotations

import os

import pytest

from app import migrate as m
from tests.support import cardio_schema as cs
from tests.support.dataapi_backend import BACKENDS, raw_mysql, use_dataapi
from tests.support.migrations import recreate_schema_with_migrations
from tests.support.sql_capture import ddl, recorded_sql, writes

EXPECTED_CREATE_PREFIX = "CREATE TABLE IF NOT EXISTS cardio_recommendations ("
TABLE = cs.TABLE


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
    database = f"{base[: -len('_test')]}_cardio025_test"
    recreate_schema_with_migrations(database=database, **_kwargs())
    return database


@pytest.fixture
def sraw(schema):
    with raw_mysql(database=schema, **_kwargs()) as conn:
        cs.drop_table(conn)
        yield conn


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
        return cs.apply_025(conn, dry_run=dry_run)
    finally:
        conn.close()


def _admin(raw) -> int:
    with raw.cursor() as cur:
        cur.execute("INSERT INTO users (username, password_hash, role, is_active) VALUES (%s, 'x', 'admin', 1)", (f"adm-{os.urandom(4).hex()}",))
        cur.execute("SELECT id FROM users WHERE role = 'admin' AND is_active = 1 ORDER BY id ASC LIMIT 1")
        return cur.fetchone()["id"]


def test_banco_sem_tabela_cria_formato_final_e_reexecucao_e_zero(backend, sraw, monkeypatch, fake_rds):
    planned = _run(dry_run=True)
    assert len(planned) == 1 and planned[0].startswith(EXPECTED_CREATE_PREFIX)
    assert cs.shape(sraw)["columns"] == []  # dry-run nao cria nada

    with recorded_sql(backend, monkeypatch, fake_rds) as sent:
        applied = _run()
    assert applied == planned
    assert len(ddl(sent)) == 1  # um statement por chamada, sem multi-statement
    cs.assert_final_shape(sraw)

    with recorded_sql(backend, monkeypatch, fake_rds) as sent:
        assert _run() == []
    assert writes(sent) == []


def test_tabela_do_guard_antigo_completa_e_zero_statements(backend, sraw, monkeypatch, fake_rds):
    cs.run_sql(sraw, cs.LEGACY_FULL_SQL)
    cs.assert_final_shape(sraw)  # guard antigo == formato medido em dev == formato da 025

    with recorded_sql(backend, monkeypatch, fake_rds) as sent:
        assert _run() == []
    assert writes(sent) == []
    cs.assert_final_shape(sraw)


def test_tabela_de_versao_antiga_do_guard_recebe_so_o_que_falta(backend, sraw, monkeypatch, fake_rds):
    admin_id = _admin(sraw)
    cs.run_sql(sraw, [cs.LEGACY_CREATE_SQL])
    with sraw.cursor() as cur:
        for i in range(2):
            cur.execute(
                "INSERT INTO cardio_recommendations (reference_date, recommendation_markdown) VALUES ('2026-09-01', %s)",
                (f"# legado {i}",),
            )

    applied = _run()
    assert applied == [
        f"ALTER TABLE {TABLE} ADD COLUMN user_id BIGINT NULL AFTER id",
        f"ALTER TABLE {TABLE} ADD COLUMN workout_plan_id BIGINT NULL AFTER reference_date",
        f"ALTER TABLE {TABLE} ADD INDEX idx_cardio_recommendations_plan (workout_plan_id)",
        f"ALTER TABLE {TABLE} ADD CONSTRAINT fk_cardio_recommendations_workout_plan FOREIGN KEY (workout_plan_id) "
        "REFERENCES workout_plans(id) ON DELETE SET NULL",
        f"UPDATE {TABLE} SET user_id = %s WHERE user_id IS NULL",
        f"ALTER TABLE {TABLE} MODIFY COLUMN user_id BIGINT NOT NULL",
    ]
    cs.assert_final_shape(sraw)
    with sraw.cursor() as cur:
        cur.execute("SELECT DISTINCT user_id FROM cardio_recommendations")
        assert [row["user_id"] for row in cur.fetchall()] == [admin_id]
    assert _run() == []


def _model_varchar_255(raw):
    cs.run_sql(raw, [cs.LEGACY_CREATE_SQL, f"ALTER TABLE {TABLE} MODIFY COLUMN model VARCHAR(255) NULL"])


def _user_id_int(raw):
    cs.run_sql(raw, [cs.LEGACY_CREATE_SQL, f"ALTER TABLE {TABLE} ADD COLUMN user_id INT NULL AFTER id"])


def _fk_cascade(raw):
    sql = list(cs.LEGACY_FULL_SQL)
    sql[2] = sql[2].replace("ON DELETE SET NULL", "ON DELETE CASCADE")
    cs.run_sql(raw, sql)


def _orphan_without_fk(raw):
    cs.run_sql(raw, [s for s in cs.LEGACY_FULL_SQL if "FOREIGN KEY" not in s])
    with raw.cursor() as cur:
        cur.execute("SELECT COALESCE(MAX(id), 0) + 1000 AS n FROM workout_plans")
        missing_plan = cur.fetchone()["n"]
        cur.execute(
            "INSERT INTO cardio_recommendations (user_id, reference_date, workout_plan_id, recommendation_markdown) "
            "VALUES (1, '2026-09-01', %s, '# orfao')",
            (missing_plan,),
        )


def _null_user_without_admin(raw):
    cs.run_sql(raw, [cs.LEGACY_CREATE_SQL])
    with raw.cursor() as cur:
        cur.execute("UPDATE users SET is_active = 0 WHERE role = 'admin'")
        cur.execute("INSERT INTO cardio_recommendations (reference_date, recommendation_markdown) VALUES ('2026-09-01', '# sem dono')")


def _date_index_on_other_column(raw):
    cs.run_sql(
        raw,
        [
            cs.LEGACY_CREATE_SQL.replace("KEY idx_cardio_recommendations_date (reference_date)", "KEY idx_cardio_recommendations_date (generated_at)"),
        ],
    )


INCOMPATIBLE = {
    "model-varchar-255": (_model_varchar_255, r"cardio_recommendations\.model e varchar\(255\)"),
    "user_id-int": (_user_id_int, r"cardio_recommendations\.user_id e int"),
    "fk-on-delete-cascade": (_fk_cascade, r"aponta para \('workout_plans', 'id', 'CASCADE'\)"),
    "orfao-sem-fk": (_orphan_without_fk, r"1 linha\(s\) com workout_plan_id sem plano"),
    "user_id-null-sem-admin": (_null_user_without_admin, r"1 linha\(s\) com user_id NULL e nenhum admin ativo"),
    "indice-date-em-outra-coluna": (_date_index_on_other_column, r"indice idx_cardio_recommendations_date existe em \['generated_at'\]"),
}


@pytest.mark.parametrize("case", sorted(INCOMPATIBLE))
def test_estado_incompativel_falha_sem_aplicar_nada(backend, sraw, monkeypatch, fake_rds, case):
    arrange, message = INCOMPATIBLE[case]
    arrange(sraw)
    before = cs.shape(sraw)

    with recorded_sql(backend, monkeypatch, fake_rds) as sent:
        with pytest.raises(Exception, match=message) as exc_info:
            _run()
    assert "Nenhum statement executado" in str(exc_info.value)
    assert type(exc_info.value).__name__ == "CardioRecommendationsSchemaError"
    assert writes(sent) == []
    assert cs.shape(sraw) == before

    with sraw.cursor() as cur:  # nao contamina os proximos casos
        cur.execute("UPDATE users SET is_active = 1 WHERE role = 'admin'")


def test_runner_registra_025_com_create_em_banco_sem_tabela(sraw, monkeypatch, fake_rds, schema):
    """Pelo runner (`app.migrate`), caminho Data API: 022..025 aplicadas; a 025
    planeja e executa exatamente o CREATE; segunda execucao nao aplica nada."""
    with sraw.cursor() as cur:
        for table in (m.CONTROL_TABLE, m.LOCK_TABLE):
            cur.execute(f"DROP TABLE IF EXISTS `{table}`")
    use_dataapi(monkeypatch, fake_rds, schema)
    monkeypatch.delenv("MIGRATIONS_DIR", raising=False)

    report = m.migrate()
    applied = {a.filename: a.statements for a in report.applied}
    assert cs.MIGRATION_FILENAME in applied
    assert len(applied[cs.MIGRATION_FILENAME]) == 1 and applied[cs.MIGRATION_FILENAME][0].startswith(EXPECTED_CREATE_PREFIX)
    # A cadeia continua depois da 025: a 098 (M98 C3a) acrescenta as 2 colunas
    # da recomendacao estruturada, num ALTER so.
    assert len(applied["098_recomendacao_cardio_estruturada.py"]) == 1
    cs.assert_final_shape(sraw, with_098=True)
    assert m.migrate().applied == []
    assert fake_rds.open_transactions == []
