"""Client `rds-data` compartilhado por (modulo boto3, regiao) em
`app/db_dataapi.py` (F1.6, desempenho da visao geral).

O que protege:

- duas `Connection` no mesmo processo e regiao reusam o MESMO client (nao
  ha um `boto3.client(...)` por conexao);
- regiao diferente -> client diferente;
- modulo `boto3` diferente (o duble dos testes troca o modulo via
  monkeypatch) -> client diferente, nunca um client velho apontando para um
  duble ja encerrado;
- o client de rollback "fast-fail" continua proprio (config sem retries).
"""

from __future__ import annotations

from types import SimpleNamespace

from app import db_dataapi


class _FakeClient:
    def __init__(self, region: str, config=None) -> None:
        self.region = region
        self.config = config
        self.begun = 0

    def begin_transaction(self, **_kw):
        self.begun += 1
        return {"transactionId": f"tx-{self.begun}"}

    def rollback_transaction(self, **_kw):
        return {}

    def commit_transaction(self, **_kw):
        return {}


def _fake_module(created: list):
    def _client(service_name: str, region_name: str = "sa-east-1", config=None):
        assert service_name == "rds-data"
        client = _FakeClient(region_name, config)
        created.append(client)
        return client

    return SimpleNamespace(client=_client)


def _conn(region: str) -> db_dataapi.Connection:
    return db_dataapi.Connection("arn:cluster", "arn:secret", "db", region)


def test_conexoes_da_mesma_regiao_compartilham_o_client(monkeypatch):
    created: list = []
    monkeypatch.setattr(db_dataapi, "boto3", _fake_module(created))

    a = _conn("sa-east-1")
    b = _conn("sa-east-1")
    assert a.client is b.client
    assert len(created) == 1
    assert created[0].begun == 2  # cada conexao ainda abre a PROPRIA transacao
    assert a.transaction_id != b.transaction_id


def test_regiao_diferente_usa_client_diferente(monkeypatch):
    created: list = []
    monkeypatch.setattr(db_dataapi, "boto3", _fake_module(created))

    a = _conn("sa-east-1")
    b = _conn("us-east-1")
    assert a.client is not b.client
    assert {c.region for c in created} == {"sa-east-1", "us-east-1"}


def test_modulo_boto3_diferente_nao_reaproveita_client_velho(monkeypatch):
    primeiro: list = []
    monkeypatch.setattr(db_dataapi, "boto3", _fake_module(primeiro))
    a = _conn("sa-east-1")

    segundo: list = []
    monkeypatch.setattr(db_dataapi, "boto3", _fake_module(segundo))
    b = _conn("sa-east-1")

    assert a.client is not b.client
    assert len(primeiro) == 1 and len(segundo) == 1


def test_fast_fail_client_continua_proprio(monkeypatch):
    created: list = []
    monkeypatch.setattr(db_dataapi, "boto3", _fake_module(created))
    conn = _conn("sa-east-1")
    rapido = conn._fast_fail_client()
    assert rapido is not conn.client
    assert rapido.config is not None
