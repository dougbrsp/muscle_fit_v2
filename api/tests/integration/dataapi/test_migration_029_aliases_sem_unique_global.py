"""Migracao 029 (`exercise_aliases` sem UNIQUE global por `alias`) nos dois
backends - PyMySQL e o caminho de PRODUCAO (shim `app/db_dataapi.py` + dublê da
RDS Data API):

- banco com o indice (DEV/CI, 001..028): plano exato, um statement, indices de
  `exercise_aliases` iguais ao recorte da Aurora; reexecucao com 0 statements;
- banco sem o indice (formato da Aurora): 0 statements, sem escrita;
- sem UNIQUE (exercise_id, alias): falha sem aplicar.

O runner 001..029 em banco novo saiu daqui no M38 (2a passada): a cadeia
inteira roda UMA vez por backend em
`test_runner_cadeia_completa.py::test_runner_001_029_em_banco_novo_aplica_a_029`,
com as MESMAS assercoes. O plano e as leituras de indice vivem em
`tests/support/aliases_029.py`, compartilhados pelos dois arquivos.
"""

from __future__ import annotations

import os
import shutil

import pymysql
import pytest

from app import migrate as m
from app.db_pymysql import Connection as PyMySQLConnection
from tests.support.aliases_029 import MIGRATION_029 as MIGRATION_PATH
from tests.support.aliases_029 import PLAN, aliases_indexes, aurora_aliases_indexes
from tests.support.dataapi_backend import BACKENDS, raw_mysql, use_dataapi
from tests.support.migrations import recreate_schema_with_migrations
from tests.support.sql_capture import ddl, recorded_sql, writes


def _kwargs() -> dict:
    return dict(
        host=os.environ["MYSQL_HOST"],
        port=int(os.environ["MYSQL_PORT"]),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
    )


def _pymysql_factory(database: str):
    kw = _kwargs()
    return lambda: PyMySQLConnection(kw["host"], kw["port"], kw["user"], kw["password"], database)


def _has_index(raw, name: str) -> bool:
    return any(index == name for index, *_ in aliases_indexes(raw))


@pytest.fixture(scope="module")
def schema(tmp_path_factory) -> str:
    """Banco novo de CI ate a 028 (runner), ainda com o UNIQUE global."""
    base = os.environ["MYSQL_DATABASE"]
    database = f"{base[: -len('_test')]}_p029_test"
    recreate_schema_with_migrations(database=database, **_kwargs())
    until_028 = tmp_path_factory.mktemp("migrations_028")
    for path in m.DEFAULT_MIGRATIONS_DIR.iterdir():
        match = m.MIGRATION_FILENAME_RE.match(path.name)
        if match and int(match.group("version")) <= 28:
            shutil.copy2(path, until_028 / path.name)
    m.migrate(conn_factory=_pymysql_factory(database), migrations_dir=until_028)
    return database


@pytest.fixture
def sraw(schema):
    """Cada teste parte do formato de DEV/CI: os dois UNIQUE presentes."""
    with raw_mysql(database=schema, **_kwargs()) as conn:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE exercise_aliases")
            if not _has_index(conn, "uq_exercise_aliases_exercise_alias"):
                cur.execute("ALTER TABLE exercise_aliases ADD UNIQUE KEY uq_exercise_aliases_exercise_alias (exercise_id, alias)")
            if not _has_index(conn, "uq_exercise_aliases_alias"):
                cur.execute("ALTER TABLE exercise_aliases ADD UNIQUE KEY uq_exercise_aliases_alias (alias)")
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
        facade = m.MigrationDB(conn, dry_run=dry_run)
        m.load_python_migration(MIGRATION_PATH)(facade)
        if not dry_run:
            conn.commit()
        return facade.statements
    finally:
        conn.close()


def test_banco_com_o_indice_remove_so_o_unique_global_e_fica_igual_a_aurora(backend, sraw, monkeypatch, fake_rds):
    assert aliases_indexes(sraw) != aurora_aliases_indexes()
    assert _run(dry_run=True) == PLAN
    assert _has_index(sraw, "uq_exercise_aliases_alias")  # dry-run nao altera nada

    with recorded_sql(backend, monkeypatch, fake_rds) as sent:
        assert _run() == PLAN
    assert ddl(sent) == PLAN  # um statement por chamada
    assert writes(sent) == PLAN
    assert aliases_indexes(sraw) == aurora_aliases_indexes()

    with recorded_sql(backend, monkeypatch, fake_rds) as sent:
        assert _run() == []
    assert writes(sent) == []
    assert [s for s in sent if "information_schema" not in s.lower()] == []  # nem contagem de linhas


def test_banco_sem_o_indice_executa_zero_statements(backend, sraw, monkeypatch, fake_rds):
    with sraw.cursor() as cur:
        cur.execute("ALTER TABLE exercise_aliases DROP INDEX uq_exercise_aliases_alias")
    assert aliases_indexes(sraw) == aurora_aliases_indexes()

    with recorded_sql(backend, monkeypatch, fake_rds) as sent:
        assert _run() == []
    assert writes(sent) == []


def test_sem_unique_por_exercicio_falha_sem_aplicar(backend, sraw):
    with sraw.cursor() as cur:
        cur.execute("ALTER TABLE exercise_aliases DROP INDEX uq_exercise_aliases_exercise_alias")
    with pytest.raises(Exception, match="Nenhum statement executado"):
        _run()
    assert _has_index(sraw, "uq_exercise_aliases_alias")


def test_depois_da_029_o_schema_aceita_apelido_repetido_so_em_outro_exercicio(backend, sraw):
    """Mesmo comportamento de schema da Aurora (rotas em
    tests/integration/dataapi/test_aliases_duplicados_paridade.py)."""
    _run()
    with sraw.cursor() as cur:
        cur.execute("INSERT IGNORE INTO exercise_catalog (id, name) VALUES (9101, 'Supino 029'), (9102, 'Crucifixo 029')")
        cur.execute("INSERT INTO exercise_aliases (exercise_id, alias) VALUES (9101, 'apelido 029'), (9102, 'apelido 029')")
        with pytest.raises(pymysql.err.IntegrityError, match="1062"):
            cur.execute("INSERT INTO exercise_aliases (exercise_id, alias) VALUES (9101, 'APELIDO 029')")
        cur.execute("TRUNCATE TABLE exercise_aliases")
