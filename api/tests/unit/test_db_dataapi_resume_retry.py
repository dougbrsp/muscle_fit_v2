"""Unit (sem MySQL, sem AWS) do retry de `DatabaseResumingException` no shim
`app/db_dataapi.py`.

Contexto: com o auto-pause do Aurora Serverless v2 (`MinCapacity = 0`), a
PRIMEIRA chamada da Data API depois de uma pausa falha com
`DatabaseResumingException` (HTTP 400, `senderFault: true`) - que o boto3 NAO
retenta. Estes testes fixam o contrato do retry: so essa excecao, so nas
operacoes que a declaram, com teto de espera previsivel.

O `sleep` e substituido por um dublê, entao a suite e instantanea.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError

from app import db_dataapi

RESUMING_MESSAGE = (
    "A request was cancelled because the Aurora Serverless v2 DB instance was paused."
)


def _client_error(code: str, operation: str, message: str = "msg") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": message}}, operation)


def _resuming(operation: str) -> ClientError:
    return _client_error("DatabaseResumingException", operation, RESUMING_MESSAGE)


class _StubClient:
    """Dublê do client boto3 `rds-data`.

    `begin_errors`/`execute_errors` sao filas de excecoes: cada chamada consome
    a proxima; quando a fila acaba (ou a entrada e `None`), a chamada tem
    sucesso. Assim da para montar "falha N vezes e depois funciona".
    """

    def __init__(
        self,
        begin_errors: list[Exception] | None = None,
        execute_errors: list[Exception] | None = None,
        commit_errors: list[Exception] | None = None,
    ):
        self._begin_errors = list(begin_errors or [])
        self._execute_errors = list(execute_errors or [])
        self._commit_errors = list(commit_errors or [])
        self.begin_calls = 0
        self.execute_calls = 0
        self.commit_calls = 0
        self.rollback_calls = 0

    @staticmethod
    def _next(queue: list[Exception]) -> None:
        if queue:
            error = queue.pop(0)
            if error is not None:
                raise error

    def begin_transaction(self, **_kw):
        self.begin_calls += 1
        self._next(self._begin_errors)
        return {"transactionId": f"tx-{self.begin_calls}"}

    def execute_statement(self, **_kw):
        self.execute_calls += 1
        self._next(self._execute_errors)
        return {
            "columnMetadata": [{"label": "n", "typeName": "INT"}],
            "records": [[{"longValue": 7}]],
        }

    def commit_transaction(self, **_kw):
        self.commit_calls += 1
        self._next(self._commit_errors)
        return {"transactionStatus": "Transaction Committed"}

    def rollback_transaction(self, **_kw):
        self.rollback_calls += 1
        return {"transactionStatus": "Rollback Complete"}


@pytest.fixture
def env(monkeypatch):
    """Ambiente do shim + `_sleep` substituido: registra cada espera pedida."""
    holder = SimpleNamespace(client=_StubClient(), sleeps=[])

    monkeypatch.setattr(
        db_dataapi, "boto3", SimpleNamespace(client=lambda *_a, **_kw: holder.client)
    )
    monkeypatch.setattr(db_dataapi, "_sleep", holder.sleeps.append)
    monkeypatch.setenv("AURORA_CLUSTER_ARN", "arn:aws:rds:sa-east-1:000000000000:cluster:unit")
    monkeypatch.setenv("AURORA_SECRET_ARN", "arn:aws:secretsmanager:sa-east-1:000000000000:secret:unit")
    monkeypatch.setenv("AURORA_DATABASE", "unit")
    return holder


def _max_attempts() -> int:
    return len(db_dataapi._RESUME_RETRY_WAITS) + 1


# ---------------------------------------------------------------------------
# Parametros: o teto de espera precisa caber no `timeout = 29` do api-core.
# ---------------------------------------------------------------------------


def test_parametros_dentro_do_teto_de_espera():
    assert _max_attempts() == 5
    assert sum(db_dataapi._RESUME_RETRY_WAITS) <= db_dataapi._RESUME_MAX_TOTAL_WAIT_SECONDS
    assert db_dataapi._RESUME_MAX_TOTAL_WAIT_SECONDS <= 20.0
    # Esperas CURTAS e uniformes: o resume tipico (~15 s) precisa ser percebido
    # logo depois de terminar, nao na proxima janela longa.
    assert set(db_dataapi._RESUME_RETRY_WAITS) == {5.0}


# ---------------------------------------------------------------------------
# Falha N vezes e depois sucesso
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("falhas", [1, 2, 3, 4])
def test_begin_transaction_retenta_e_conclui(env, falhas):
    env.client = _StubClient(begin_errors=[_resuming("BeginTransaction")] * falhas)

    conn = db_dataapi.get_conn()

    assert conn.transaction_id == f"tx-{falhas + 1}"
    assert env.client.begin_calls == falhas + 1
    assert env.sleeps == list(db_dataapi._RESUME_RETRY_WAITS[:falhas])


@pytest.mark.parametrize("falhas", [1, 2, 3, 4])
def test_execute_statement_retenta_e_devolve_resultado(env, falhas):
    env.client = _StubClient(execute_errors=[_resuming("ExecuteStatement")] * falhas)

    conn = db_dataapi.get_conn()
    cur = conn.cursor().execute("SELECT 1 AS n")

    assert cur.fetchone() == {"n": 7}
    assert env.client.execute_calls == falhas + 1
    assert env.sleeps == list(db_dataapi._RESUME_RETRY_WAITS[:falhas])


# ---------------------------------------------------------------------------
# Falha em TODAS as tentativas: propaga a excecao original
# ---------------------------------------------------------------------------


def test_begin_transaction_esgota_tentativas_e_propaga_original(env):
    erros = [_resuming("BeginTransaction") for _ in range(_max_attempts())]
    env.client = _StubClient(begin_errors=list(erros))

    with pytest.raises(ClientError) as exc_info:
        db_dataapi.get_conn()

    assert exc_info.value is erros[-1]
    assert env.client.begin_calls == _max_attempts()
    assert env.sleeps == list(db_dataapi._RESUME_RETRY_WAITS)
    assert sum(env.sleeps) <= db_dataapi._RESUME_MAX_TOTAL_WAIT_SECONDS


def test_execute_statement_esgota_tentativas_e_preserva_causa(env):
    erros = [_resuming("ExecuteStatement") for _ in range(_max_attempts())]
    env.client = _StubClient(execute_errors=list(erros))

    conn = db_dataapi.get_conn()
    with pytest.raises(db_dataapi.DataApiError) as exc_info:
        conn.cursor().execute("SELECT 1 AS n")

    # `Cursor.execute` embrulha em DataApiError (comportamento existente); a
    # excecao original continua acessivel como causa, nao e engolida.
    assert exc_info.value.__cause__ is erros[-1]
    assert env.client.execute_calls == _max_attempts()
    assert env.sleeps == list(db_dataapi._RESUME_RETRY_WAITS)


# ---------------------------------------------------------------------------
# Qualquer outra excecao: NAO e retentada
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "erro",
    [
        _client_error("BadRequestException", "ExecuteStatement", "Invalid parameter"),
        _client_error("StatementTimeoutException", "ExecuteStatement"),
        _client_error("ThrottlingException", "ExecuteStatement"),
        RuntimeError("erro qualquer"),
    ],
    ids=["bad-request", "statement-timeout", "throttling", "nao-aws"],
)
def test_outra_excecao_nao_e_retentada_no_execute(env, erro):
    env.client = _StubClient(execute_errors=[erro, erro, erro])

    conn = db_dataapi.get_conn()
    with pytest.raises(db_dataapi.DataApiError) as exc_info:
        conn.cursor().execute("SELECT 1 AS n")

    assert exc_info.value.__cause__ is erro
    assert env.client.execute_calls == 1
    assert env.sleeps == []


def test_outra_excecao_nao_e_retentada_no_begin(env):
    erro = _client_error("DatabaseNotFoundException", "BeginTransaction")
    env.client = _StubClient(begin_errors=[erro, erro, erro])

    with pytest.raises(ClientError) as exc_info:
        db_dataapi.get_conn()

    assert exc_info.value is erro
    assert env.client.begin_calls == 1
    assert env.sleeps == []


def test_is_database_resuming_error_so_casa_o_codigo_exato():
    assert db_dataapi._is_database_resuming_error(_resuming("ExecuteStatement")) is True
    # Mensagem parecida, codigo diferente: nao conta.
    assert (
        db_dataapi._is_database_resuming_error(
            _client_error("BadRequestException", "ExecuteStatement", RESUMING_MESSAGE)
        )
        is False
    )
    assert db_dataapi._is_database_resuming_error(RuntimeError(RESUMING_MESSAGE)) is False


# ---------------------------------------------------------------------------
# Caminho deixado DE FORA de proposito: commit/rollback nao declaram
# `DatabaseResumingException` no modelo do servico, entao nao sao retentados.
# ---------------------------------------------------------------------------


def test_commit_transaction_nao_e_retentado(env):
    erro = _resuming("CommitTransaction")
    env.client = _StubClient(commit_errors=[erro, erro, erro])

    conn = db_dataapi.get_conn()
    with pytest.raises(ClientError) as exc_info:
        conn.commit()

    assert exc_info.value is erro
    assert env.client.commit_calls == 1
    assert env.sleeps == []
