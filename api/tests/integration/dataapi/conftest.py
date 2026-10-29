"""Fixtures da suite `dataapi`: caminho de producao do banco (RDS Data API)
exercitado contra o dublê `tests/support/fake_rds_data.py`, apoiado no MySQL
de teste da suite de integracao (schema `TEST_MYSQL_DATABASE`).

- `fake_rds`: dublê do servico (um por teste; transacoes abertas sao
  descartadas no teardown).
- `dataapi_db`: aplicacao inteira apontada para `app/db_dataapi.py` + dublê.
- `db_backend`: parametrizada em `pymysql` e `data-api` - o MESMO teste roda
  nos dois backends; `backend_results` guarda o resultado de cada um para
  comparacao (divergencia = falha).
- `raw`: conexao neutra (PyMySQL direto) para semear/conferir estado.
"""

from __future__ import annotations

import os

import pytest

from tests.support.dataapi_backend import BACKENDS, raw_mysql, use_backend, use_dataapi
from tests.support.fake_rds_data import FakeRdsDataBackend


def _mysql_kwargs() -> dict:
    return dict(
        host=os.environ["MYSQL_HOST"],
        port=int(os.environ["MYSQL_PORT"]),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
    )


@pytest.fixture
def mysql_kwargs() -> dict:
    return _mysql_kwargs()


@pytest.fixture
def fake_rds(mysql_schema):
    fake = FakeRdsDataBackend(**_mysql_kwargs())
    yield fake
    fake.close()


@pytest.fixture
def dataapi_db(monkeypatch, fake_rds, mysql_schema) -> FakeRdsDataBackend:
    use_dataapi(monkeypatch, fake_rds, mysql_schema)
    return fake_rds


@pytest.fixture
def raw(mysql_schema):
    with raw_mysql(database=mysql_schema, **_mysql_kwargs()) as conn:
        yield conn


@pytest.fixture(params=BACKENDS)
def db_backend(request, monkeypatch, fake_rds, mysql_schema) -> str:
    use_backend(request.param, monkeypatch, fake_rds, mysql_schema)
    return request.param
