"""Migracao 037 (congelamento da prescricao) nos dois backends - PyMySQL e o
caminho de PRODUCAO (shim `app/db_dataapi.py` + dublê da RDS Data API):

- banco sem as colunas: 2 statements (um ALTER por tabela), um por chamada;
- formato final igual ao que o plano promete (tipos, nulabilidade, default e
  o indice de `progression_decision_id`);
- reexecucao: 0 statements e nenhuma escrita;
- estado parcial: so o que falta;
- formato inesperado: falha antes do primeiro statement, schema intacto.

O runner (`app.migrate`) aplicando 001..037 num banco novo foi para
`test_runner_cadeia_completa.py` no M38 (2a passada).

Schema proprio (`*_p11_test`), apagado ao final.
"""

from __future__ import annotations

import os

import pytest

from app import migrate as m
from app.db_pymysql import Connection as PyMySQLConnection
from tests.support.dataapi_backend import BACKENDS, raw_mysql, use_dataapi
from tests.support.migrations import recreate_schema_with_migrations
from tests.support.prescricao_037 import ALTER_SETS, ALTER_WPE, DECISION_INDEX, SETS, WPE, apply
from tests.support.sql_capture import ddl, recorded_sql, writes

NEW_COLUMNS = {
    WPE: ("prescription_json", "progression_decision_id", "engine_version", "requires_user_choice"),
    SETS: ("rpe_target_effective", "safety_event"),
}


def _kwargs() -> dict:
    return dict(
        host=os.environ["MYSQL_HOST"],
        port=int(os.environ["MYSQL_PORT"]),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
    )


def _database(tag: str) -> str:
    base = os.environ["MYSQL_DATABASE"]
    return f"{base[: -len('_test')]}_{tag}_test"


def _drop_database(database: str) -> None:
    assert database.endswith("_test")
    with raw_mysql(database="information_schema", **_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS `{database}`")


def _pymysql_factory(database: str):
    kw = _kwargs()
    return lambda: PyMySQLConnection(kw["host"], kw["port"], kw["user"], kw["password"], database)


@pytest.fixture(scope="module")
def schema():
    database = _database("p11")
    recreate_schema_with_migrations(database=database, **_kwargs())
    m.migrate(conn_factory=_pymysql_factory(database))
    yield database
    _drop_database(database)


@pytest.fixture
def sraw(schema):
    """Cada teste parte do formato final (037 aplicada) e devolve o schema
    nesse mesmo estado, pela conexao neutra - independente do backend."""
    with raw_mysql(database=schema, **_kwargs()) as conn:
        yield conn
        with conn.cursor() as cur:
            for table, columns in NEW_COLUMNS.items():
                existing = set(_columns(conn, table))
                drops = [f"DROP COLUMN {column}" for column in columns if column in existing]
                if drops:
                    cur.execute(f"ALTER TABLE {table} " + ", ".join(drops))
        conn.commit()
        neutra = _pymysql_factory(schema)()
        try:
            apply(neutra)
        finally:
            neutra.close()


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
        return apply(conn, dry_run=dry_run)
    finally:
        conn.close()


def _columns(raw, table: str) -> dict[str, tuple[str, str, object]]:
    with raw.cursor() as cur:
        cur.execute(
            "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
            (table,),
        )
        return {
            r["COLUMN_NAME"]: (r["COLUMN_TYPE"].lower(), r["IS_NULLABLE"], r["COLUMN_DEFAULT"]) for r in cur.fetchall()
        }


def _index_columns(raw, table: str, index: str) -> list[str]:
    with raw.cursor() as cur:
        cur.execute(
            "SELECT COLUMN_NAME FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() "
            "AND TABLE_NAME = %s AND INDEX_NAME = %s ORDER BY SEQ_IN_INDEX",
            (table, index),
        )
        return [r["COLUMN_NAME"] for r in cur.fetchall()]


def _drop_037(raw) -> None:
    with raw.cursor() as cur:
        for table, columns in NEW_COLUMNS.items():
            cur.execute(f"ALTER TABLE {table} " + ", ".join(f"DROP COLUMN {column}" for column in columns))


def test_banco_sem_as_colunas_aplica_dois_statements_e_a_reexecucao_nao_escreve(
    backend, sraw, monkeypatch, fake_rds
):
    _drop_037(sraw)

    plano = _run(dry_run=True)
    assert plano == [ALTER_WPE, ALTER_SETS]
    assert "prescription_json" not in _columns(sraw, WPE)  # dry-run nao altera nada

    with recorded_sql(backend, monkeypatch, fake_rds) as sent:
        assert _run() == plano
    assert len(ddl(sent)) == 2 and len(writes(sent)) == 2  # um statement por chamada

    wpe = _columns(sraw, WPE)
    assert wpe["prescription_json"] == ("json", "YES", None)
    assert wpe["progression_decision_id"] == ("bigint", "YES", None)
    assert wpe["engine_version"] == ("varchar(40)", "YES", None)
    assert wpe["requires_user_choice"] == ("tinyint(1)", "NO", "0")
    sets = _columns(sraw, SETS)
    assert sets["rpe_target_effective"] == ("tinyint", "YES", None)
    assert sets["safety_event"] == ("varchar(40)", "YES", None)
    assert _index_columns(sraw, WPE, DECISION_INDEX) == ["progression_decision_id"]

    with recorded_sql(backend, monkeypatch, fake_rds) as sent:
        assert _run() == []
    assert writes(sent) == []


def test_linha_existente_nao_muda_de_significado(backend, sraw):
    """`requires_user_choice` e a unica NOT NULL: o default 0 vale para o que
    ja estava gravado, e o resto fica NULL (= ficha legada)."""
    _drop_037(sraw)
    with sraw.cursor() as cur:
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES ('p11-legado', 'x', 'admin')")
        user_id = cur.lastrowid
        cur.execute(
            "INSERT INTO workout_plans (user_id, plan_date, name, status) VALUES (%s, CURDATE(), 'Legada', 'planned')",
            (user_id,),
        )
        plan_id = cur.lastrowid
        cur.execute(
            "INSERT INTO workout_plan_exercises (plan_id, sequence, exercise_name) VALUES (%s, 1, 'Supino')",
            (plan_id,),
        )
    sraw.commit()

    _run()

    with sraw.cursor() as cur:
        cur.execute(
            "SELECT prescription_json, engine_version, requires_user_choice FROM workout_plan_exercises "
            "WHERE plan_id = %s",
            (plan_id,),
        )
        row = cur.fetchone()
        cur.execute("DELETE FROM workout_plans WHERE id = %s", (plan_id,))
        cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
    sraw.commit()
    assert row["prescription_json"] is None and row["engine_version"] is None
    assert int(row["requires_user_choice"]) == 0


def test_so_adiciona_o_que_falta(backend, sraw):
    with sraw.cursor() as cur:
        cur.execute(f"ALTER TABLE {WPE} DROP COLUMN engine_version")
        cur.execute(f"ALTER TABLE {SETS} DROP COLUMN safety_event")
    assert _run() == [
        f"ALTER TABLE {WPE} ADD COLUMN engine_version VARCHAR(40) NULL",
        f"ALTER TABLE {SETS} ADD COLUMN safety_event VARCHAR(40) NULL",
    ]
    assert _run() == []


def test_formato_inesperado_falha_sem_aplicar(backend, sraw):
    with sraw.cursor() as cur:
        cur.execute(f"ALTER TABLE {SETS} DROP COLUMN safety_event")
        cur.execute(f"ALTER TABLE {WPE} MODIFY COLUMN engine_version VARCHAR(10) NULL")
    with pytest.raises(Exception, match="037: .*engine_version e varchar\\(10\\).*Nenhum statement executado"):
        _run()
    assert "safety_event" not in _columns(sraw, SETS)  # nada aplicado

    with sraw.cursor() as cur:
        cur.execute(f"ALTER TABLE {WPE} MODIFY COLUMN engine_version VARCHAR(40) NULL")
    assert _run() == [f"ALTER TABLE {SETS} ADD COLUMN safety_event VARCHAR(40) NULL"]


# O runner 001..037 em banco novo saiu daqui no M38 (2a passada): a cadeia
# inteira roda UMA vez por backend em
# `test_runner_cadeia_completa.py::test_runner_em_banco_novo_aplica_a_037`, com
# as MESMAS assercoes.
