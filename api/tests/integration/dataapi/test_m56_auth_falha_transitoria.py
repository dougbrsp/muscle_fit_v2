"""M56: falha ao consultar a sessao responde 503 e NAO invalida a sessao -
contra MySQL real, nos DOIS backends (PyMySQL de dev e shim da RDS Data API
de producao).

Incidente: o usuario foi mandado para o login sem ter saido. Antes do M56,
`security._session_identity` engolia qualquer excecao de banco e devolvia
None, que `resolve_identity` transformava em 401 - e o frontend trata 401
como sessao encerrada.

O que este arquivo prova, em ordem de risco:

- **sessao valida -> 200, sessao desconhecida -> 401** (o 401 legitimo nao
  mudou);
- **falha de banco -> 503 com `Retry-After`**, nos dois backends, com a
  falha REAL de cada driver: no shim, a `DatabaseResumingException` do
  auto-pause do Aurora esgotando o retry de `db_dataapi` e um
  `InternalServerErrorException` da Data API; no PyMySQL, conexao recusada;
- **a sessao sobrevive**: depois da falha, a mesma sessao volta a responder
  200 e `user_sessions.revoked_at` continua NULL (conferido por conexao
  neutra).
"""

from __future__ import annotations

import hashlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Importados no topo de proposito: `use_backend` so troca o backend de
# modulos `app.*` JA importados (ver tests/support/dataapi_backend.py).
from app import auth, db_dataapi, security
from tests.support.isolamento_v3 import truncar

TABELAS = ("user_sessions", "devices", "users")
TOKEN = "sessao-m56-token-cru-nunca-logado"
USERNAME = "usuario-m56"


@pytest.fixture
def sessao(raw):
    """Usuario ativo + sessao valida por 30 dias. Trunca antes e depois."""
    truncar(raw, TABELAS)
    with raw.cursor() as cur:
        cur.execute(
            "INSERT INTO users (username, password_hash, role, is_active) VALUES (%s, 'x', 'regular', 1)",
            (USERNAME,),
        )
        user_id = cur.lastrowid
        cur.execute(
            "INSERT INTO user_sessions (user_id, token_hash, expires_at) "
            "VALUES (%s, %s, DATE_ADD(NOW(), INTERVAL 30 DAY))",
            (user_id, hashlib.sha256(TOKEN.encode("utf-8")).hexdigest()),
        )
        session_id = cur.lastrowid
    raw.commit()
    yield {"user_id": user_id, "session_id": session_id}
    truncar(raw, TABELAS)


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(auth.router)
    return TestClient(app, raise_server_exceptions=False)


def _me(client: TestClient, token: str = TOKEN):
    # Mesmo caminho do frontend web: cookie httpOnly de sessao.
    return client.get("/auth/me", headers={"Cookie": f"{security.SESSION_COOKIE_NAME}={token}"})


def _revoked_at(raw, session_id: int):
    with raw.cursor() as cur:
        cur.execute("SELECT revoked_at FROM user_sessions WHERE id = %s", (session_id,))
        return cur.fetchone()["revoked_at"]


def _quebrar_banco(db_backend: str, monkeypatch, fake_rds, caso: str) -> None:
    if db_backend == "data-api":
        if caso == "resume_esgotado":
            # Auto-pause: o retry de `db_dataapi` roda de verdade (5 tentativas),
            # so sem dormir. Esgotado, a excecao chega na resolucao de
            # identidade (a 1a consulta e a de dispositivo, depois a sessao).
            monkeypatch.setattr(db_dataapi, "_sleep", lambda _s: None)
            fake_rds.inject_error(
                "BeginTransaction",
                "DatabaseResumingException",
                "Aurora is resuming after being auto-paused",
                times=len(db_dataapi._RESUME_RETRY_WAITS) + 1,
            )
        else:
            fake_rds.inject_error(
                "ExecuteStatement", "InternalServerErrorException", "Internal error", status=500
            )
    else:
        # PyMySQL le a configuracao a cada conexao: porta 1 = conexao recusada.
        monkeypatch.setenv("MYSQL_PORT", "1")


@pytest.mark.parametrize("caso", ["resume_esgotado", "erro_interno"])
def test_falha_de_banco_responde_503_e_preserva_a_sessao(db_backend, raw, sessao, client, monkeypatch, fake_rds, caso):
    assert _me(client).status_code == 200, db_backend

    with monkeypatch.context() as quebra:
        _quebrar_banco(db_backend, quebra, fake_rds, caso)
        resposta = _me(client)

    assert resposta.status_code == 503, (db_backend, caso, resposta.text)
    assert resposta.json()["detail"] == security.AUTH_BACKEND_UNAVAILABLE_DETAIL
    assert resposta.headers["Retry-After"] == str(security.AUTH_BACKEND_RETRY_AFTER_SECONDS)

    # A sessao continua valida: nada foi revogado e a proxima chamada passa.
    assert _revoked_at(raw, sessao["session_id"]) is None, db_backend
    depois = _me(client)
    assert depois.status_code == 200, (db_backend, caso, depois.text)
    assert depois.json()["username"] == USERNAME


def test_sessao_desconhecida_continua_401(db_backend, raw, sessao, client):
    resposta = _me(client, token="token-que-nao-existe")
    assert resposta.status_code == 401, db_backend
    assert resposta.json()["detail"] == "Token inválido."


def test_sessao_revogada_continua_401(db_backend, raw, sessao, client):
    with raw.cursor() as cur:
        cur.execute("UPDATE user_sessions SET revoked_at = NOW() WHERE id = %s", (sessao["session_id"],))
    raw.commit()
    assert _me(client).status_code == 401, db_backend
