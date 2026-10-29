"""Unit (sem MySQL/sem rede) do tratamento de rollback do shim
`app/db_dataapi.py` (Achado B). O caminho completo, com client boto3 real e
dublê da Data API, fica em tests/integration/dataapi/."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError

from app import db_dataapi


def _client_error(code: str, message: str = "msg", operation: str = "RollbackTransaction") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": message}}, operation)


class _StubClient:
    def __init__(self, rollback_error: Exception | None = None):
        self.rollback_error = rollback_error
        self.rollbacks: list[str] = []

    def begin_transaction(self, **_kw):
        return {"transactionId": f"tx-{len(self.rollbacks)}"}

    def rollback_transaction(self, **kw):
        self.rollbacks.append(kw["transactionId"])
        if self.rollback_error is not None:
            raise self.rollback_error
        return {"transactionStatus": "Rollback Complete"}

    def commit_transaction(self, **_kw):
        return {"transactionStatus": "Transaction Committed"}


@pytest.fixture
def stub(monkeypatch):
    holder = SimpleNamespace(client=_StubClient(), configs=[])

    def _client(*_a, **kw):
        holder.configs.append(kw.get("config"))
        return holder.client

    monkeypatch.setattr(db_dataapi, "boto3", SimpleNamespace(client=_client))
    monkeypatch.setenv("AURORA_CLUSTER_ARN", "arn:aws:rds:sa-east-1:000000000000:cluster:unit")
    monkeypatch.setenv("AURORA_SECRET_ARN", "arn:aws:secretsmanager:sa-east-1:000000000000:secret:unit")
    monkeypatch.setenv("AURORA_DATABASE", "unit")
    return holder


@pytest.mark.parametrize(
    "error, gone",
    [
        (_client_error("TransactionNotFoundException", "The transaction ID wasn't found"), True),
        (_client_error("BadRequestException", "Transaction abc isn't found"), True),
        (_client_error("NotFoundException", "Transaction abc is not found"), True),
        (_client_error("BadRequestException", "Invalid parameter"), False),
        (_client_error("ThrottlingException", "Rate exceeded"), False),
        (RuntimeError("Transaction abc is not found"), False),
    ],
)
def test_is_transaction_gone_error(error, gone):
    assert db_dataapi._is_transaction_gone_error(error) is gone


def test_rollback_transacao_inexistente_nao_levanta_e_zera(stub):
    stub.client = _StubClient(_client_error("TransactionNotFoundException"))
    conn = db_dataapi.get_conn()
    conn.rollback()
    assert conn.transaction_id is None
    conn.close()
    assert stub.client.rollbacks == ["tx-0"]


def test_rollback_outra_falha_propaga_e_zera(stub):
    stub.client = _StubClient(_client_error("ThrottlingException"))
    conn = db_dataapi.get_conn()
    with pytest.raises(ClientError):
        conn.rollback()
    assert conn.transaction_id is None
    conn.close()
    assert len(stub.client.rollbacks) == 1


@pytest.mark.parametrize("rollback_error", [None, _client_error("TransactionNotFoundException")])
def test_transaction_preserva_original_sem_nota_quando_rollback_ok_ou_ja_revertido(stub, rollback_error):
    stub.client = _StubClient(rollback_error)
    original = db_dataapi.DataApiError("Duplicate entry 'x' for key 'uq' (1062)")
    with pytest.raises(db_dataapi.DataApiError) as exc:
        with db_dataapi.transaction():
            raise original
    assert exc.value is original
    assert not getattr(exc.value, "__notes__", None)


@pytest.mark.parametrize("entrada", ["transaction", "with-conn"])
def test_rollback_com_falha_real_anexa_contexto_a_original(stub, entrada):
    stub.client = _StubClient(_client_error("ThrottlingException"))
    original = db_dataapi.DataApiError("Duplicate entry 'x' for key 'uq' (1062)")
    block = db_dataapi.transaction() if entrada == "transaction" else db_dataapi.get_conn()
    with pytest.raises(db_dataapi.DataApiError) as exc:
        with block as conn:
            raise original
    assert exc.value is original
    assert any("ThrottlingException" in note for note in exc.value.__notes__)
    assert conn.transaction_id is None
    assert len(stub.client.rollbacks) == 1
    assert stub.configs == [None]


def _unsupported_result_error() -> db_dataapi.DataApiError:
    """DataApiError como `Cursor.execute` produz (encapsulando o ClientError)."""
    cause = _client_error("UnsupportedResultException", "The result exceeds the size limit 1 MB.", "ExecuteStatement")
    try:
        try:
            raise cause
        except ClientError as exc:
            raise db_dataapi.DataApiError(f"execute_statement falhou para SQL: 'SELECT ...' -- {exc}") from exc
    except db_dataapi.DataApiError as err:
        return err


@pytest.mark.parametrize("padrao", ["try-finally-close", "except-rollback-raise"])
def test_rollback_via_close_ou_except_nao_mascara_a_original(stub, padrao):
    stub.client = _StubClient(_client_error("InternalServerErrorException"))
    original = _unsupported_result_error()
    with pytest.raises(db_dataapi.DataApiError) as exc:
        conn = db_dataapi.get_conn()
        try:
            if padrao == "except-rollback-raise":
                try:
                    raise original
                except Exception:
                    conn.rollback()
                    raise
            raise original
        finally:
            conn.close()
    assert exc.value is original
    assert any("InternalServerErrorException" in note for note in exc.value.__notes__)
    assert conn.transaction_id is None
    assert len(stub.client.rollbacks) == 1
    assert stub.configs[0] is None  # client normal do construtor
    assert stub.configs[-1].retries == {"total_max_attempts": 1}  # rollback sem retries


def test_rollback_sem_excecao_em_andamento_propaga_e_usa_client_padrao(stub):
    stub.client = _StubClient(_client_error("InternalServerErrorException"))
    conn = db_dataapi.get_conn()
    with pytest.raises(ClientError):
        conn.close()
    assert conn.transaction_id is None
    assert stub.configs == [None]


def test_falha_ao_criar_client_sem_retries_nao_mascara_a_original(stub, monkeypatch):
    def _client(*_a, **kw):
        if kw.get("config") is not None:
            raise RuntimeError("falha ao criar client")
        return stub.client

    monkeypatch.setattr(db_dataapi, "boto3", SimpleNamespace(client=_client))
    original = _unsupported_result_error()
    with pytest.raises(db_dataapi.DataApiError) as exc:
        conn = db_dataapi.get_conn()
        try:
            raise original
        finally:
            conn.close()
    assert exc.value is original
    assert any("RuntimeError" in note for note in exc.value.__notes__)
    assert conn.transaction_id is None


def test_original_aws_error_code_le_a_causa_encapsulada():
    assert db_dataapi._original_aws_error_code(_unsupported_result_error()) == "UnsupportedResultException"
    assert db_dataapi._original_aws_error_code(RuntimeError("x")) is None
    assert db_dataapi._original_aws_error_code(None) is None
