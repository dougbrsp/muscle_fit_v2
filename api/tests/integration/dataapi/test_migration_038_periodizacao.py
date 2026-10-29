"""Migracao 038 (auditoria da periodizacao) nos dois backends - PyMySQL e o
caminho de PRODUCAO (shim `app/db_dataapi.py` + dublê da RDS Data API):

- banco sem a tabela nem as colunas: 2 statements (CREATE + ALTER), um por
  chamada, sem PREPARE/multi-statement;
- formato final igual ao que o plano promete (colunas, defaults, indices, FKs);
- linha existente de `workout_plan_templates` fica `origin='manual'` (nenhuma
  linha muda de significado);
- reexecucao: 0 statements e nenhuma escrita;
- estado parcial: so o que falta;
- formato inesperado: falha antes do primeiro statement, schema intacto.

O runner (`app.migrate`) aplicando 001..038 num banco novo foi para
`test_runner_cadeia_completa.py` no M38 (2a passada).

Schema proprio (`*_p16_test`), apagado ao final.
"""

from __future__ import annotations

import os

import pytest

from app import migrate as m
from app.db_pymysql import Connection as PyMySQLConnection
from tests.support.dataapi_backend import BACKENDS, raw_mysql, use_dataapi
from tests.support.migrations import recreate_schema_with_migrations
from tests.support.periodizacao_038 import (
    ALTER_TEMPLATES,
    GENERATION_LOG_INDEX,
    LOGS,
    LOGS_TEMPLATE_INDEX,
    LOGS_USER_INDEX,
    TEMPLATES,
    apply,
    module,
)
from tests.support.sql_capture import ddl, recorded_sql, writes

CREATE_LOGS = " ".join(module()["CREATE_LOGS_SQL"].split())
NEW_TEMPLATE_COLUMNS = ("origin", "generation_log_id")


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
    database = _database("p16")
    recreate_schema_with_migrations(database=database, **_kwargs())
    m.migrate(conn_factory=_pymysql_factory(database))
    yield database
    _drop_database(database)


@pytest.fixture
def sraw(schema):
    """Cada teste parte do formato final (038 aplicada) e devolve o schema nesse
    mesmo estado, pela conexao neutra - independente do backend."""
    with raw_mysql(database=schema, **_kwargs()) as conn:
        yield conn
        _drop_038(conn, tolerante=True)
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
            r["COLUMN_NAME"]: (r["COLUMN_TYPE"].lower(), r["IS_NULLABLE"], r["COLUMN_DEFAULT"])
            for r in cur.fetchall()
        }


def _index_columns(raw, table: str, index: str) -> list[str]:
    with raw.cursor() as cur:
        cur.execute(
            "SELECT COLUMN_NAME FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() "
            "AND TABLE_NAME = %s AND INDEX_NAME = %s ORDER BY SEQ_IN_INDEX",
            (table, index),
        )
        return [r["COLUMN_NAME"] for r in cur.fetchall()]


def _foreign_keys(raw, table: str) -> dict[str, tuple[str, str]]:
    with raw.cursor() as cur:
        cur.execute(
            "SELECT k.COLUMN_NAME, k.REFERENCED_TABLE_NAME, r.DELETE_RULE "
            "FROM information_schema.KEY_COLUMN_USAGE k "
            "JOIN information_schema.REFERENTIAL_CONSTRAINTS r ON r.CONSTRAINT_SCHEMA = k.CONSTRAINT_SCHEMA "
            "AND r.CONSTRAINT_NAME = k.CONSTRAINT_NAME AND r.TABLE_NAME = k.TABLE_NAME "
            "WHERE k.TABLE_SCHEMA = DATABASE() AND k.TABLE_NAME = %s AND k.REFERENCED_TABLE_NAME IS NOT NULL",
            (table,),
        )
        return {r["COLUMN_NAME"]: (r["REFERENCED_TABLE_NAME"], r["DELETE_RULE"]) for r in cur.fetchall()}


def _drop_038(raw, tolerante: bool = False) -> None:
    existing = set(_columns(raw, TEMPLATES))
    drops = [f"DROP COLUMN {column}" for column in NEW_TEMPLATE_COLUMNS if column in existing]
    with raw.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {LOGS}")
        if drops:
            cur.execute(f"ALTER TABLE {TEMPLATES} " + ", ".join(drops))
        elif not tolerante:
            raise AssertionError("estado inesperado: colunas da 038 ausentes antes do teste")
    raw.commit()


def test_banco_sem_a_tabela_aplica_dois_statements_e_a_reexecucao_nao_escreve(
    backend, sraw, monkeypatch, fake_rds
):
    _drop_038(sraw)

    plano = _run(dry_run=True)
    assert plano == [CREATE_LOGS, ALTER_TEMPLATES]
    assert LOGS not in _tabelas(sraw)  # dry-run nao altera nada

    with recorded_sql(backend, monkeypatch, fake_rds) as sent:
        assert _run() == plano
    assert len(ddl(sent)) == 2 and len(writes(sent)) == 2  # um statement por chamada

    logs = _columns(sraw, LOGS)
    assert logs["user_id"] == ("bigint", "NO", None)
    assert logs["status"] == ("varchar(20)", "NO", "pending")
    assert logs["cost_usd"] == ("decimal(8,4)", "YES", None)
    assert logs["request_json"] == ("json", "YES", None)
    assert logs["saved_template_id"] == ("bigint", "YES", None)
    assert _index_columns(sraw, LOGS, LOGS_USER_INDEX) == ["user_id", "created_at"]
    assert _index_columns(sraw, LOGS, LOGS_TEMPLATE_INDEX) == ["saved_template_id"]
    assert _foreign_keys(sraw, LOGS) == {
        "user_id": ("users", "CASCADE"),
        "saved_template_id": (TEMPLATES, "SET NULL"),
    }

    templates = _columns(sraw, TEMPLATES)
    assert templates["origin"] == ("enum('manual','periodization')", "NO", "manual")
    assert templates["generation_log_id"] == ("bigint", "YES", None)
    assert _index_columns(sraw, TEMPLATES, GENERATION_LOG_INDEX) == ["generation_log_id"]
    # FK logica: nenhuma constraint fisica nova em workout_plan_templates
    assert "generation_log_id" not in _foreign_keys(sraw, TEMPLATES)

    with recorded_sql(backend, monkeypatch, fake_rds) as sent:
        assert _run() == []
    assert writes(sent) == []


def test_modelo_existente_fica_manual(backend, sraw):
    """`origin` e a unica NOT NULL: o default 'manual' vale para o que ja estava
    gravado - nenhum modelo muda de significado."""
    _drop_038(sraw)
    with sraw.cursor() as cur:
        cur.execute("INSERT INTO users (username, password_hash, role) VALUES ('p16-legado', 'x', 'admin')")
        user_id = cur.lastrowid
        cur.execute(
            "INSERT INTO workout_plan_templates (name, created_by_user_id) VALUES ('Modelo legado', %s)",
            (user_id,),
        )
        template_id = cur.lastrowid
    sraw.commit()

    _run()

    with sraw.cursor() as cur:
        cur.execute(
            "SELECT origin, generation_log_id FROM workout_plan_templates WHERE id = %s", (template_id,)
        )
        row = cur.fetchone()
        cur.execute("DELETE FROM workout_plan_templates WHERE id = %s", (template_id,))
        cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
    sraw.commit()
    assert row["origin"] == "manual"
    assert row["generation_log_id"] is None


def test_so_adiciona_o_que_falta(backend, sraw):
    with sraw.cursor() as cur:
        cur.execute(f"ALTER TABLE {TEMPLATES} DROP COLUMN generation_log_id")
    sraw.commit()
    assert _run() == [
        f"ALTER TABLE {TEMPLATES} ADD COLUMN generation_log_id BIGINT NULL, "
        f"ADD KEY {GENERATION_LOG_INDEX} (generation_log_id)"
    ]
    assert _run() == []


def test_formato_inesperado_falha_sem_aplicar(backend, sraw):
    with sraw.cursor() as cur:
        cur.execute(f"DROP TABLE {LOGS}")
        cur.execute(f"ALTER TABLE {TEMPLATES} MODIFY COLUMN origin VARCHAR(20) NOT NULL DEFAULT 'manual'")
    sraw.commit()
    with pytest.raises(Exception, match="038: .*origin e varchar\\(20\\).*Nenhum statement executado"):
        _run()
    assert LOGS not in _tabelas(sraw)  # nada aplicado

    with sraw.cursor() as cur:
        cur.execute(
            f"ALTER TABLE {TEMPLATES} MODIFY COLUMN origin ENUM('manual','periodization') NOT NULL DEFAULT 'manual'"
        )
    sraw.commit()
    assert _run() == [CREATE_LOGS]


def test_limite_diario_le_o_indice_de_usuario_e_data(backend, sraw):
    """A consulta do limite de 10/dia roda no caminho de producao (shim Data API
    incluido) e conta so as geracoes do usuario no dia."""
    from app.periodization.logs import count_generations_today, create_pending_log

    import app.db as db

    conn = db.get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO users (username, password_hash, role) VALUES ('p16-limite', 'x', 'admin')")
            user_id = cur.lastrowid
        assert count_generations_today(conn, user_id) == 0
        create_pending_log(conn, user_id, {"rag_sources": ["2. ABC"], "instructions": "x"}, "periodization-v1")
        create_pending_log(conn, user_id, {"rag_sources": []}, "periodization-v1")
        conn.commit()
        assert count_generations_today(conn, user_id) == 2
        assert count_generations_today(conn, user_id + 9999) == 0
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE id = %s", (user_id,))  # CASCADE limpa os logs
        conn.commit()
    finally:
        conn.close()


# O runner 001..038 em banco novo saiu daqui no M38 (2a passada): a cadeia
# inteira roda UMA vez por backend em
# `test_runner_cadeia_completa.py::test_runner_em_banco_novo_aplica_a_038`, com
# as MESMAS assercoes.


def _tabelas(raw) -> set[str]:
    with raw.cursor() as cur:
        cur.execute(
            "SELECT TABLE_NAME FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() "
            "AND TABLE_TYPE = 'BASE TABLE'"
        )
        return {row["TABLE_NAME"] for row in cur.fetchall()}
