"""M56 (unit, sem banco): "sessao inexistente" continua 401; "falha ao
consultar" vira 503 e nao desloga.

O banco e trocado por um dublê de `transaction()` que ou responde (com ou
sem linha) ou levanta. A prova contra MySQL real, nos dois backends, esta em
`tests/integration/dataapi/test_m56_auth_falha_transitoria.py`.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import auth, security
from tests.support.fake_db import CursorFactoryConn

TOKEN = "token-cru-que-nunca-pode-aparecer-no-log"


class _Cursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        return self

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None


def _banco_sem_linhas():
    @contextmanager
    def _transaction():
        yield CursorFactoryConn(_Cursor, [])

    return _transaction


def _banco_fora(exc: Exception):
    @contextmanager
    def _transaction():
        raise exc
        yield  # pragma: no cover

    return _transaction


@pytest.fixture
def log_seguranca(caplog):
    caplog.set_level(logging.ERROR, logger="fitness.security")
    return caplog


@pytest.fixture(autouse=True)
def sem_token_mestre(monkeypatch):
    monkeypatch.delenv("API_TOKEN", raising=False)


# ---------------------------------------------------------------------------
# _session_identity: os dois ramos
# ---------------------------------------------------------------------------


def test_sessao_inexistente_ou_expirada_devolve_none(monkeypatch):
    monkeypatch.setattr(security, "transaction", _banco_sem_linhas())
    assert security._session_identity(TOKEN) is None


def test_usuario_inativo_devolve_none(monkeypatch):
    linha = {"session_id": 3, "user_id": 7, "role": "regular", "is_active": 0}

    @contextmanager
    def _transaction():
        yield CursorFactoryConn(_Cursor, [linha])

    monkeypatch.setattr(security, "transaction", _transaction)
    assert security._session_identity(TOKEN) is None


def test_sessao_valida_devolve_identidade(monkeypatch):
    linha = {"session_id": 3, "user_id": 7, "role": "regular", "is_active": 1}

    @contextmanager
    def _transaction():
        yield CursorFactoryConn(_Cursor, [linha])

    monkeypatch.setattr(security, "transaction", _transaction)
    assert security._session_identity(TOKEN) == security.Identity(user_id=7, role="regular", source="session")


def test_falha_ao_consultar_sessao_e_503_com_log_sem_token(monkeypatch, log_seguranca):
    monkeypatch.setattr(security, "transaction", _banco_fora(RuntimeError("DatabaseResumingException")))

    with pytest.raises(security.AuthBackendUnavailable) as exc:
        security._session_identity(TOKEN)

    assert exc.value.status_code == 503
    assert exc.value.detail == security.AUTH_BACKEND_UNAVAILABLE_DETAIL
    assert exc.value.headers == {"Retry-After": str(security.AUTH_BACKEND_RETRY_AFTER_SECONDS)}

    registros = [r for r in log_seguranca.records if r.name == "fitness.security"]
    assert len(registros) == 1
    registro = registros[0]
    assert registro.levelno == logging.ERROR
    assert registro.exc_info is not None, "o log precisa da stack"
    assert registro.step == "session"
    assert TOKEN not in log_seguranca.text
    assert security.hashlib.sha256(TOKEN.encode()).hexdigest() not in log_seguranca.text


def test_auth_backend_unavailable_e_http_exception():
    # Vale em qualquer app FastAPI (inclusive os minimos dos testes), sem
    # exception handler registrado.
    assert issubclass(security.AuthBackendUnavailable, HTTPException)


# ---------------------------------------------------------------------------
# resolve_identity: nenhum outro except converte erro de banco em 401
# ---------------------------------------------------------------------------


def test_resolve_identity_token_desconhecido_e_401(monkeypatch):
    monkeypatch.setattr(security, "transaction", _banco_sem_linhas())
    with pytest.raises(HTTPException) as exc:
        security.resolve_identity(TOKEN)
    assert exc.value.status_code == 401


def test_resolve_identity_sem_token_e_401_sem_tocar_o_banco(monkeypatch):
    monkeypatch.setattr(security, "transaction", _banco_fora(AssertionError("nao devia consultar")))
    with pytest.raises(HTTPException) as exc:
        security.resolve_identity(None)
    assert exc.value.status_code == 401


def test_resolve_identity_banco_fora_e_503(monkeypatch):
    monkeypatch.setattr(security, "transaction", _banco_fora(ConnectionError("Data API indisponivel")))
    with pytest.raises(HTTPException) as exc:
        security.resolve_identity(TOKEN)
    assert exc.value.status_code == 503


def test_dispositivo_banco_fora_e_503_e_nao_false(monkeypatch):
    monkeypatch.setattr(security, "transaction", _banco_fora(ConnectionError("Data API indisponivel")))
    with pytest.raises(security.AuthBackendUnavailable):
        security.device_token_matches(TOKEN)


def test_token_mestre_com_banco_fora_e_503(monkeypatch):
    monkeypatch.setenv("API_TOKEN", TOKEN)
    monkeypatch.setattr(security, "transaction", _banco_fora(ConnectionError("Data API indisponivel")))
    with pytest.raises(security.AuthBackendUnavailable):
        security.resolve_identity(TOKEN)


def test_require_token_dispositivo_com_banco_fora_e_503(monkeypatch):
    monkeypatch.setenv("API_TOKEN", "outro-token-mestre")
    monkeypatch.setattr(security, "transaction", _banco_fora(ConnectionError("Data API indisponivel")))
    with pytest.raises(HTTPException) as exc:
        security.require_token(TOKEN)
    assert exc.value.status_code == 503


# ---------------------------------------------------------------------------
# Contrato de GET /auth/me (o guard do frontend)
# ---------------------------------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(auth.router)
    return TestClient(app, raise_server_exceptions=False)


def _me(client: TestClient, token: str = TOKEN):
    return client.get("/auth/me", headers={"Cookie": f"{security.SESSION_COOKIE_NAME}={token}"})


def test_contrato_auth_me_sem_sessao_e_401(client, monkeypatch):
    monkeypatch.setattr(security, "transaction", _banco_sem_linhas())
    resposta = _me(client)
    assert resposta.status_code == 401
    assert resposta.json() == {"detail": "Token inválido."}


def test_contrato_auth_me_sem_cookie_e_401(client):
    resposta = client.get("/auth/me")
    assert resposta.status_code == 401


def test_contrato_auth_me_banco_fora_e_503_com_retry_after(client, monkeypatch):
    monkeypatch.setattr(security, "transaction", _banco_fora(ConnectionError("Data API indisponivel")))
    resposta = _me(client)
    assert resposta.status_code == 503
    assert resposta.json() == {"detail": security.AUTH_BACKEND_UNAVAILABLE_DETAIL}
    assert resposta.headers["Retry-After"] == str(security.AUTH_BACKEND_RETRY_AFTER_SECONDS)
