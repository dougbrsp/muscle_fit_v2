"""Troca, SO dentro do processo de teste, a camada de dados da aplicacao do
backend PyMySQL (forcado em tests/conftest.py) para o shim de producao
`app/db_dataapi.py`, apontado para o dublê `tests/support/fake_rds_data.py`.

Ponto de injecao: apenas `app.db_dataapi.boto3` (o shim faz
`boto3.client("rds-data", region_name=...)` no construtor da `Connection`).
Nenhuma linha de producao muda: o caminho executado e o mesmo da Lambda
(`Connection` -> `begin_transaction` -> `Cursor.execute` -> `_translate_sql`
-> `execute_statement`), so que o client boto3 fala com o dublê.

Por que nao reimportar `app.db` com `DB_BACKEND=data-api`: `app/db.py` escolhe
o backend no import e varios modulos fazem `from .db import transaction` no
topo. Reimportar deixaria referencias antigas espalhadas. Em vez disso, todo
atributo de modulo `app.*` que aponte para um objeto de `app.db_pymysql`
(`get_conn`, `transaction`, `DataApiError`, ...) e trocado pelo equivalente de
`app.db_dataapi` via `monkeypatch` (desfeito ao fim do teste). A escolha do
dispatcher em si e verificada em subprocesso
(tests/integration/dataapi/test_shim_contrato.py::test_dispatcher_*).
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from typing import Any, Iterator

import pymysql
import pymysql.cursors

from tests.support.fake_rds_data import FAKE_CLUSTER_ARN, FAKE_REGION, FAKE_SECRET_ARN, FakeRdsDataBackend

BACKENDS = ("pymysql", "data-api")


def _swap_targets() -> tuple[Any, Any]:
    from app import db_dataapi, db_pymysql

    return db_pymysql, db_dataapi


def use_dataapi(monkeypatch: Any, fake: FakeRdsDataBackend, database: str) -> None:
    """Aponta a aplicacao inteira para o shim Data API + dublê."""
    import app.db as db

    db_pymysql, db_dataapi = _swap_targets()
    if db.get_conn is not db_pymysql.get_conn:
        raise AssertionError("app.db deveria estar no backend pymysql antes da troca (tests/conftest.py)")

    monkeypatch.setenv("DB_BACKEND", "data-api")
    monkeypatch.setenv("AURORA_CLUSTER_ARN", FAKE_CLUSTER_ARN)
    monkeypatch.setenv("AURORA_SECRET_ARN", FAKE_SECRET_ARN)
    monkeypatch.setenv("AURORA_DATABASE", database)
    monkeypatch.setenv("AWS_REGION", FAKE_REGION)
    monkeypatch.setattr(db_dataapi, "boto3", fake.boto3_module())

    for module_name, module in list(sys.modules.items()):
        if module is None or module in (db_pymysql, db_dataapi):
            continue
        if module_name != "app" and not module_name.startswith("app."):
            continue
        for name in db.__all__:
            if getattr(module, name, None) is getattr(db_pymysql, name):
                monkeypatch.setattr(module, name, getattr(db_dataapi, name))


def use_backend(backend: str, monkeypatch: Any, fake: FakeRdsDataBackend, database: str) -> None:
    if backend == "data-api":
        use_dataapi(monkeypatch, fake, database)
    elif backend != "pymysql":
        raise ValueError(backend)


@contextmanager
def raw_mysql(host: str, port: int, user: str, password: str, database: str) -> Iterator[pymysql.connections.Connection]:
    """Conexao NEUTRA (PyMySQL direto, autocommit, fora da camada da
    aplicacao) para semear dados e conferir o estado gravado, igual para os
    dois backends."""
    conn = pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        charset="utf8mb4",
        autocommit=True,
        cursorclass=pymysql.cursors.DictCursor,
    )
    try:
        yield conn
    finally:
        conn.close()
