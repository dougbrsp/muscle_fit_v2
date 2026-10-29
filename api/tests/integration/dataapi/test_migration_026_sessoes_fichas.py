"""Migracao 026 (colunas de sessoes de treino e fichas) nos dois backends -
PyMySQL e o caminho de PRODUCAO (shim `app/db_dataapi.py` + dublê da RDS Data
API) - num schema proprio (`<TEST_MYSQL_DATABASE sem _test>_ssf026_test`,
001..021 aplicadas).

- banco sem os objetos: um ALTER por coluna (com indice/FK junto), formato
  final igual ao da Aurora/dev; reexecucao com 0 statements;
- baseline 001..021 (banco novo de dev/CI): so as duas colunas que so os
  guards criavam;
- estado criado pelos guards antigos (esperado na Aurora e em dev): 0
  statements e nenhuma escrita;
- estados antigos parciais: so o que falta;
- estado incompativel: falha antes do primeiro statement, schema intacto;
- runner (`app.migrate`) aplicando 001..026.
"""

from __future__ import annotations

import os

import pymysql
import pytest

from app import migrate as m
from tests.support import sessoes_fichas_schema as ssf
from tests.support.dataapi_backend import BACKENDS, raw_mysql, use_dataapi
from tests.support.migrations import recreate_schema_with_migrations, undo_reshaping_migrations
from tests.support.sql_capture import ddl, recorded_sql, writes


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
    database = f"{base[: -len('_test')]}_ssf026_test"
    recreate_schema_with_migrations(database=database, **_kwargs())
    return database


@pytest.fixture
def sraw(schema):
    """Cada teste parte do formato final (026 aplicada), sem linhas."""
    with raw_mysql(database=schema, **_kwargs()) as conn:
        ssf.truncate_tables(conn)
        ssf.drop_columns(conn, ssf.OBJECT_COLUMNS)
        ssf.apply_026(conn)
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
        return ssf.apply_026(conn, dry_run=dry_run)
    finally:
        conn.close()


def _user_and_plan(raw) -> tuple[int, int]:
    with raw.cursor() as cur:
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES (%s, 'x', 'admin')", (f"u-{os.urandom(4).hex()}",))
        user_id = cur.lastrowid
        cur.execute("INSERT INTO workout_plans (user_id, name) VALUES (%s, 'Ficha')", (user_id,))
        return user_id, cur.lastrowid


def test_banco_sem_os_objetos_cria_formato_final_e_reexecucao_e_zero(backend, sraw, monkeypatch, fake_rds):
    ssf.drop_columns(sraw, ssf.OBJECT_COLUMNS)
    before = ssf.shape(sraw)
    assert before == {"columns": [], "indexes": [], "fks": []}

    planned = _run(dry_run=True)
    assert planned == ssf.EMPTY_PLAN
    assert ssf.shape(sraw) == before  # dry-run nao cria nada

    with recorded_sql(backend, monkeypatch, fake_rds) as sent:
        assert _run() == planned
    # um statement por chamada, sem multi-statement (`ddl()` resume cada um em 120 caracteres)
    assert ddl(sent) == [statement[:120] for statement in planned]
    ssf.assert_final_shape(sraw)

    with recorded_sql(backend, monkeypatch, fake_rds) as sent:
        assert _run() == []
    assert writes(sent) == []


def test_baseline_001_a_021_recebe_so_as_colunas_que_so_os_guards_criavam(backend, sraw):
    ssf.drop_columns(sraw, ssf.GUARD_ONLY_COLUMNS)
    assert _run() == ssf.BASELINE_PLAN
    ssf.assert_final_shape(sraw)


def test_estado_dos_guards_antigos_e_zero_statements(backend, sraw, monkeypatch, fake_rds):
    ssf.drop_columns(sraw, ssf.GUARD_ONLY_COLUMNS)
    ssf.run_sql(sraw, ssf.LEGACY_GUARD_SQL)
    ssf.assert_final_shape(sraw)  # guard antigo == formato da Aurora/dev == formato da 026

    with recorded_sql(backend, monkeypatch, fake_rds) as sent:
        assert _run() == []
    assert writes(sent) == []
    assert [s for s in sent if "information_schema" not in s.lower()] == []  # so leituras de schema
    ssf.assert_final_shape(sraw)


def test_estado_antigo_parcial_recebe_so_o_que_falta(backend, sraw):
    # Pre-v15/v17 e sem os guards: sem split_day, sem dor por serie, sem vinculos de sessao.
    ssf.drop_columns(
        sraw,
        [
            ("activities", "workout_plan_id"),
            ("exercise_sets", "muscle_pain"),
            ("exercise_sets", "joint_pain"),
            ("exercises", "workout_plan_exercise_id"),
            ("workout_plans", "split_day"),
        ],
    )
    assert _run() == [
        ssf.ADD_WORKOUT_PLAN_ID,
        "ALTER TABLE exercise_sets ADD COLUMN muscle_pain TINYINT NULL AFTER hr_max_bpm",
        "ALTER TABLE exercise_sets ADD COLUMN joint_pain TINYINT(1) NULL AFTER muscle_pain",
        ssf.ADD_WORKOUT_PLAN_EXERCISE_ID,
        "ALTER TABLE workout_plans ADD COLUMN split_day VARCHAR(1) NULL AFTER plan_type",
    ]
    ssf.assert_final_shape(sraw)
    assert _run() == []


def test_coluna_sem_indice_e_fk_com_dados_validos_recebe_indice_e_fk(backend, sraw):
    user_id, plan_id = _user_and_plan(sraw)
    ssf.drop_columns(sraw, [("activities", "workout_plan_id")])
    ssf.run_sql(sraw, ["ALTER TABLE activities ADD COLUMN workout_plan_id BIGINT NULL AFTER conducted_by"])
    with sraw.cursor() as cur:
        cur.execute(
            "INSERT INTO activities (user_id, local_date, name, workout_plan_id) VALUES (%s, '2026-09-14', 'Treino', %s)",
            (user_id, plan_id),
        )

    assert _run() == [
        "ALTER TABLE activities ADD INDEX idx_activities_workout_plan_id (workout_plan_id)",
        "ALTER TABLE activities ADD CONSTRAINT fk_activities_workout_plan FOREIGN KEY (workout_plan_id) "
        "REFERENCES workout_plans(id) ON DELETE SET NULL",
    ]
    ssf.assert_final_shape(sraw)


def test_indice_e_fk_reconhecidos_pela_coluna_com_outro_nome(backend, sraw):
    ssf.drop_columns(sraw, [("exercises", "workout_plan_exercise_id")])
    ssf.run_sql(
        sraw,
        [
            "ALTER TABLE exercises ADD COLUMN workout_plan_exercise_id BIGINT NULL AFTER mapping_id, "
            "ADD KEY idx_legado_wpe (workout_plan_exercise_id), ADD CONSTRAINT fk_legado_wpe "
            "FOREIGN KEY (workout_plan_exercise_id) REFERENCES workout_plan_exercises(id) ON DELETE SET NULL"
        ],
    )
    assert _run() == []


def test_ddl_antigo_de_mapping_id_do_guard_falha_no_mysql_8(sraw):
    """Por que a 026 NAO reproduz `_ensure_plan_link_columns` literalmente."""
    ssf.drop_columns(sraw, [("workout_plan_exercises", "mapping_id")])
    with pytest.raises(pymysql.err.MySQLError) as exc_info:
        ssf.run_sql(sraw, [ssf.LEGACY_WPE_MAPPING_SQL])
    assert exc_info.value.args[0] == 3780  # tipos incompativeis na FK


def _wpe_id_int(raw):
    ssf.drop_columns(raw, [("activities", "workout_plan_id")])
    ssf.run_sql(raw, ["ALTER TABLE activities ADD COLUMN workout_plan_id INT NULL AFTER conducted_by"])


def _hr_decimal_5_2(raw):
    ssf.drop_columns(raw, [("exercise_sets", "hr_avg_bpm")])
    ssf.run_sql(raw, ["ALTER TABLE exercise_sets ADD COLUMN hr_avg_bpm DECIMAL(5,2) NULL AFTER rest_seconds"])


def _split_day_not_null(raw):
    ssf.run_sql(raw, ["ALTER TABLE workout_plans MODIFY COLUMN split_day VARCHAR(1) NOT NULL DEFAULT 'A'"])


def _fk_cascade(raw):
    ssf.run_sql(
        raw,
        [
            "ALTER TABLE activities DROP FOREIGN KEY fk_activities_workout_plan",
            "ALTER TABLE activities ADD CONSTRAINT fk_activities_workout_plan FOREIGN KEY (workout_plan_id) "
            "REFERENCES workout_plans(id) ON DELETE CASCADE",
        ],
    )


def _orphan_without_fk(raw):
    user_id, plan_id = _user_and_plan(raw)
    # session_status ausente: o plano teria um ALTER ANTES de chegar na falha.
    ssf.drop_columns(raw, [("activities", "session_status")])
    ssf.run_sql(raw, ["ALTER TABLE activities DROP FOREIGN KEY fk_activities_workout_plan"])
    with raw.cursor() as cur:
        cur.execute(
            "INSERT INTO activities (user_id, local_date, name, workout_plan_id) VALUES (%s, '2026-09-14', 'Orfao', %s)",
            (user_id, plan_id + 1000),
        )


def _index_name_on_other_column(raw):
    ssf.drop_columns(raw, [("activities", "session_status")])
    ssf.run_sql(raw, ["ALTER TABLE activities ADD INDEX idx_activities_session_status (conducted_by)"])


def _wpe_mapping_signed(raw):
    ssf.drop_columns(raw, [("workout_plan_exercises", "mapping_id")])
    ssf.run_sql(
        raw,
        ["ALTER TABLE workout_plan_exercises ADD COLUMN mapping_id BIGINT NULL AFTER amazfit_name, ADD KEY idx_wpe_mapping (mapping_id)"],
    )


INCOMPATIBLE = {
    "workout_plan_id-int": (_wpe_id_int, r"activities\.workout_plan_id e int, esperado bigint"),
    "hr_avg_bpm-decimal-5-2": (_hr_decimal_5_2, r"exercise_sets\.hr_avg_bpm e decimal\(5,2\), esperado decimal\(6,2\)"),
    "split_day-not-null": (_split_day_not_null, r"workout_plans\.split_day tem IS_NULLABLE=NO"),
    "fk-on-delete-cascade": (_fk_cascade, r"aponta para \('workout_plans', 'id', 'CASCADE'\)"),
    "orfao-sem-fk": (_orphan_without_fk, r"1 linha\(s\) em activities\.workout_plan_id sem workout_plans"),
    "indice-em-outra-coluna": (_index_name_on_other_column, r"indice idx_activities_session_status existe em \['conducted_by'\]"),
    "wpe-mapping_id-com-sinal": (_wpe_mapping_signed, r"workout_plan_exercises\.mapping_id e bigint, esperado bigint unsigned"),
}


@pytest.mark.parametrize("case", sorted(INCOMPATIBLE))
def test_estado_incompativel_falha_sem_aplicar_nada(backend, sraw, monkeypatch, fake_rds, case):
    arrange, message = INCOMPATIBLE[case]
    arrange(sraw)
    before = ssf.shape(sraw)

    with recorded_sql(backend, monkeypatch, fake_rds) as sent:
        with pytest.raises(Exception, match=message) as exc_info:
            _run()
    assert "Nenhum statement executado" in str(exc_info.value)
    assert type(exc_info.value).__name__ == "SessoesFichasSchemaError"
    assert writes(sent) == []
    assert ssf.shape(sraw) == before


def test_runner_aplica_001_a_026(backend, sraw, schema):
    """Pelo runner (`app.migrate`), nos dois backends: baseline 001..021
    registrado, 022..026 aplicadas; a 026 executa so as duas colunas que o
    baseline nao cria; segunda execucao nao aplica nada."""
    # o runner vai reexecutar tudo: forma anterior a 068 (a 036 confere a UK)
    undo_reshaping_migrations(sraw)
    with sraw.cursor() as cur:
        for table in (m.CONTROL_TABLE, m.LOCK_TABLE):
            cur.execute(f"DROP TABLE IF EXISTS `{table}`")
    ssf.drop_columns(sraw, ssf.GUARD_ONLY_COLUMNS)
    os.environ.pop("MIGRATIONS_DIR", None)

    migrations = m.discover_migrations(m.DEFAULT_MIGRATIONS_DIR)
    report = m.migrate()
    assert report.baselined == [mig.filename for mig in migrations if mig.is_baseline]
    assert [a.filename for a in report.applied] == [mig.filename for mig in migrations if not mig.is_baseline]
    applied = {a.filename: a.statements for a in report.applied}
    assert applied[ssf.MIGRATION_FILENAME] == ssf.BASELINE_PLAN
    ssf.assert_final_shape(sraw)
    assert m.migrate().applied == []
