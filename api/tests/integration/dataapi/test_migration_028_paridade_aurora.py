"""Migracao 028 (paridade dev/CI -> Aurora) nos dois backends - PyMySQL e o
caminho de PRODUCAO (shim `app/db_dataapi.py` + dublê da RDS Data API) - num
schema proprio (`<TEST_MYSQL_DATABASE sem _test>_p028_test`) no formato de um
banco novo de CI: 001..021 + runner com 022..027.

- dev/CI: plano exato (FKs para o catalogo saem, MODIFYs, voltam no formato da
  Aurora, dono de academia NOT NULL), um statement por chamada, formato final
  igual ao recorte da Aurora; reexecucao com 0 statements e sem escrita;
- MySQL 8 real: com dados, AUTO_INCREMENT continua do maior id e as regras
  ON DELETE da Aurora passam a valer;
- dados que exigem decisao (academia sem dono, id = 0, orfao): falha antes do
  primeiro statement, schema intacto;
- retomada depois de falha no meio.
"""

from __future__ import annotations

import os
import shutil

import pymysql
import pytest

from app import migrate as m
from app.db_pymysql import Connection as PyMySQLConnection
from tests.support import paridade_028 as p
from tests.support.dataapi_backend import BACKENDS, raw_mysql, use_dataapi
from tests.support.migrations import recreate_schema_with_migrations
from tests.support.sql_capture import ddl, recorded_sql, writes

TABLES = ("exercise_legacy_id_map", "exercises", "activities", "exercise_catalog", "exercise_zepp_mappings", "gyms", "users")


def _kwargs() -> dict:
    return dict(
        host=os.environ["MYSQL_HOST"],
        port=int(os.environ["MYSQL_PORT"]),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
    )


@pytest.fixture(scope="module")
def schema(tmp_path_factory) -> dict:
    base = os.environ["MYSQL_DATABASE"]
    database = f"{base[: -len('_test')]}_p028_test"
    recreate_schema_with_migrations(database=database, **_kwargs())
    until_027 = tmp_path_factory.mktemp("migrations_027")
    for path in m.DEFAULT_MIGRATIONS_DIR.iterdir():
        match = m.MIGRATION_FILENAME_RE.match(path.name)
        if match and int(match.group("version")) <= 27:
            shutil.copy2(path, until_027 / path.name)
    kw = _kwargs()
    m.migrate(
        conn_factory=lambda: PyMySQLConnection(kw["host"], kw["port"], kw["user"], kw["password"], database),
        migrations_dir=until_027,
    )
    with raw_mysql(database=database, **kw) as conn:
        ci_shape = p.shape(conn)
    return {"database": database, "ci_shape": ci_shape}


@pytest.fixture
def sraw(schema):
    """Cada teste parte do formato de dev/CI (001..027), sem linhas."""
    with raw_mysql(database=schema["database"], **_kwargs()) as conn:
        if p.shape(conn) != schema["ci_shape"]:
            p.revert_to_dev_ci(conn)
        with conn.cursor() as cur:
            cur.execute("SET FOREIGN_KEY_CHECKS = 0")
            try:
                for table in TABLES:
                    cur.execute(f"TRUNCATE TABLE `{table}`")
            finally:
                cur.execute("SET FOREIGN_KEY_CHECKS = 1")
        assert p.shape(conn) == schema["ci_shape"]
        yield conn


@pytest.fixture(params=BACKENDS)
def backend(request, monkeypatch, fake_rds, schema) -> str:
    if request.param == "data-api":
        use_dataapi(monkeypatch, fake_rds, schema["database"])
    else:
        monkeypatch.setenv("MYSQL_DATABASE", schema["database"])
    yield request.param
    if request.param == "data-api":
        assert fake_rds.open_transactions == [], "deixou transacao Data API aberta"


def _run(dry_run: bool = False) -> list[str]:
    import app.db as db  # atributo trocado por use_dataapi

    conn = db.get_conn()
    try:
        return p.apply_028(conn, dry_run=dry_run)
    finally:
        conn.close()


def _exec(raw, sql, params=()):
    with raw.cursor() as cur:
        cur.execute(sql, params)
        return cur.lastrowid


def _one(raw, sql, params=()):
    with raw.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def _user(raw) -> int:
    return _exec(raw, "INSERT INTO users (username, password_hash, role) VALUES (%s, 'x', 'admin')", (f"u-{os.urandom(4).hex()}",))


def _exercise(raw, user_id: int, mapping_id) -> int:
    activity = _exec(raw, "INSERT INTO activities (user_id, local_date, name) VALUES (%s, CURDATE(), 'Treino')", (user_id,))
    return _exec(
        raw,
        "INSERT INTO exercises (activity_id, sequence, original_name, mapping_id) VALUES (%s, 1, 'Supino', %s)",
        (activity, mapping_id),
    )


def test_dev_ci_planeja_aplica_e_chega_ao_formato_da_aurora(backend, sraw, schema, monkeypatch, fake_rds):
    assert _run(dry_run=True) == p.DEV_CI_PLAN
    assert p.shape(sraw) == schema["ci_shape"]  # dry-run nao altera nada

    with recorded_sql(backend, monkeypatch, fake_rds) as sent:
        assert _run() == p.DEV_CI_PLAN
    # um statement por chamada, na ordem do plano, sem multi-statement
    assert ddl(sent) == [statement[:120] for statement in p.DEV_CI_PLAN]
    p.assert_aurora_shape(sraw)

    with recorded_sql(backend, monkeypatch, fake_rds) as sent:
        assert _run() == []
    assert writes(sent) == []
    assert [s for s in sent if "information_schema" not in s.lower()] == []  # nem contagem de linhas


def test_com_dados_auto_increment_segue_do_maior_id_e_regras_da_aurora_valem(backend, sraw):
    user_id = _user(sraw)
    _exec(sraw, "INSERT INTO gyms (name, owner_user_id) VALUES ('Academia', %s)", (user_id,))
    for exercise_id, name in ((5, "Crucifixo"), (114, "Supino")):
        _exec(sraw, "INSERT INTO exercise_zepp_mappings (id, exercise_name, amazfit_name) VALUES (%s, %s, %s)", (exercise_id, name, name))
        _exec(sraw, "INSERT INTO exercise_catalog (id, name) VALUES (%s, %s)", (exercise_id, name))
    linked = _exercise(sraw, user_id, 114)
    _exec(sraw, "INSERT INTO exercise_legacy_id_map (old_exercise_id, canonical_exercise_id) VALUES (900, 114)")
    with pytest.raises(pymysql.err.OperationalError, match="1364"):  # formato de dev/CI: INSERT sem id falha
        _exec(sraw, "INSERT INTO exercise_catalog (name) VALUES ('Sem id')")

    assert _run() == p.DEV_CI_PLAN

    new_id = _exec(sraw, "INSERT INTO exercise_catalog (name) VALUES ('Sem id')")
    assert new_id == 115
    assert _one(sraw, "SELECT mapping_id FROM exercises WHERE id = %s", (linked,))["mapping_id"] == 114
    # exercise_legacy_id_map: NO ACTION (Aurora), nao mais CASCADE.
    with pytest.raises(pymysql.err.IntegrityError, match="1451"):
        _exec(sraw, "DELETE FROM exercise_catalog WHERE id = 114")
    _exec(sraw, "DELETE FROM exercise_legacy_id_map WHERE old_exercise_id = 900")
    _exec(sraw, "DELETE FROM exercise_catalog WHERE id = 114")
    # exercises.mapping_id: SET NULL para o catalogo (antes apontava para o legado).
    assert _one(sraw, "SELECT mapping_id FROM exercises WHERE id = %s", (linked,))["mapping_id"] is None
    with pytest.raises(pymysql.err.IntegrityError, match="1452"):
        _exercise(sraw, user_id, 5555)


def _assert_fails_untouched(sraw, schema, message: str) -> None:
    with pytest.raises(Exception, match=message) as exc:
        _run()
    assert "Nenhum statement executado" in str(exc.value)
    assert p.shape(sraw) == schema["ci_shape"]


def test_academias_sem_dono_do_banco_novo_nao_bloqueiam_e_gyms_fica_intocada(backend, sraw):
    """Banco novo (001) semeia academias sem dono e sem usuario: a 028 roda e
    nao mexe em gyms (NOT NULL e pendencia de decisao de dado)."""
    _exec(sraw, "INSERT INTO gyms (name, owner_user_id) VALUES ('Academia semente', NULL)")
    assert _run() == p.DEV_CI_PLAN
    row = _one(
        sraw,
        "SELECT IS_NULLABLE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
        "AND TABLE_NAME = 'gyms' AND COLUMN_NAME = 'owner_user_id'",
    )
    assert row["IS_NULLABLE"] == "YES"
    assert _one(sraw, "SELECT COUNT(*) AS n FROM gyms WHERE owner_user_id IS NULL")["n"] == 1


def test_catalogo_com_id_zero_falha_sem_aplicar(backend, sraw, schema):
    _exec(sraw, "INSERT INTO exercise_catalog (id, name) VALUES (0, 'Zero')")
    _assert_fails_untouched(sraw, schema, "id = 0")


def test_exercicio_orfao_do_catalogo_falha_sem_aplicar(backend, sraw, schema):
    user_id = _user(sraw)
    _exec(sraw, "INSERT INTO exercise_zepp_mappings (id, exercise_name, amazfit_name) VALUES (777, 'Legado', 'Legado')")
    _exercise(sraw, user_id, 777)  # valido para a FK legada, sem exercise_catalog 777
    _assert_fails_untouched(sraw, schema, "1 linha\\(s\\) de exercises.mapping_id sem exercise_catalog")


def test_retomada_depois_de_falha_no_meio_completa_o_formato(backend, sraw):
    applied_before_failure = p.DEV_CI_PLAN[:11]  # FKs removidas e exercises.mapping_id convertido
    for statement in applied_before_failure:
        _exec(sraw, statement)

    assert _run() == p.DEV_CI_PLAN[11:]
    p.assert_aurora_shape(sraw)
    assert _run() == []
