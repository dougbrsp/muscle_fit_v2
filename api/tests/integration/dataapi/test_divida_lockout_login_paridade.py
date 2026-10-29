"""Paridade do registro DURAVEL da tentativa de login falha nos dois backends.

O caminho de PRODUCAO do banco e a RDS Data API (`app/db_dataapi.py`), nao o
PyMySQL usado em dev/CI de integracao. O UPDATE atomico de
`auth.register_failed_login` usa `CASE WHEN failed_login_attempts + 1 >= %s`
com um parametro TIMESTAMP dentro do CASE - exatamente o tipo de SQL que pode
se comportar diferente no shim. Este teste roda a MESMA sequencia nos dois
backends e confere o estado gravado com uma conexao neutra.

Tambem cobre o reset administrativo (`POST /users/{id}/reset-password`) nos
dois backends: hash novo, `must_change_password`, contador/lockout limpos e
sessoes revogadas.
"""

from __future__ import annotations

import hashlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import auth, users
from app.security import Identity, get_current_identity

USERNAME_ALVO = "alvo-paridade-lockout"
USERNAME_ADMIN = "admin-paridade-lockout"
SENHA_ALVO = "SenhaOriginal123"


@pytest.fixture
def contas(raw):
    """Semeia admin + alvo com uma conexao neutra (igual para os 2 backends)."""
    with raw.cursor() as cur:
        cur.execute(
            "DELETE FROM user_sessions WHERE user_id IN "
            "(SELECT id FROM users WHERE username IN (%s, %s))",
            (USERNAME_ALVO, USERNAME_ADMIN),
        )
        cur.execute("DELETE FROM users WHERE username IN (%s, %s)", (USERNAME_ALVO, USERNAME_ADMIN))
        cur.execute(
            "INSERT INTO users (username, password_hash, role, is_active) VALUES (%s, %s, 'admin', 1)",
            (USERNAME_ADMIN, auth.hash_password("senha-do-admin")),
        )
        admin_id = cur.lastrowid
        cur.execute(
            "INSERT INTO users (username, password_hash, role, is_active) VALUES (%s, %s, 'regular', 1)",
            (USERNAME_ALVO, auth.hash_password(SENHA_ALVO)),
        )
        alvo_id = cur.lastrowid
    return {"admin_id": admin_id, "alvo_id": alvo_id}


def _estado(raw, user_id: int) -> dict:
    with raw.cursor() as cur:
        cur.execute(
            "SELECT failed_login_attempts, locked_until, must_change_password, password_hash "
            "FROM users WHERE id = %s",
            (user_id,),
        )
        return cur.fetchone()


def test_register_failed_login_persiste_nos_dois_backends(db_backend, raw, contas):
    alvo_id = contas["alvo_id"]

    for esperado in range(1, auth.MAX_FAILED_ATTEMPTS):
        assert auth.register_failed_login(alvo_id) is False
        linha = _estado(raw, alvo_id)
        assert linha["failed_login_attempts"] == esperado, db_backend
        assert linha["locked_until"] is None, db_backend

    assert auth.register_failed_login(alvo_id) is True, db_backend
    linha = _estado(raw, alvo_id)
    assert linha["locked_until"] is not None, db_backend
    # Zera o contador junto com o bloqueio (tentativas cheias ao expirar).
    assert linha["failed_login_attempts"] == 0, db_backend


def test_reset_administrativo_nos_dois_backends(db_backend, raw, contas):
    alvo_id = contas["alvo_id"]

    # Uma sessao ativa e um bloqueio em vigor, para provar que o reset limpa
    # os dois.
    token_hash = hashlib.sha256(b"sessao-paridade").hexdigest()
    with raw.cursor() as cur:
        cur.execute(
            "INSERT INTO user_sessions (user_id, token_hash, expires_at) "
            "VALUES (%s, %s, DATE_ADD(NOW(), INTERVAL 30 DAY))",
            (alvo_id, token_hash),
        )
    for _ in range(auth.MAX_FAILED_ATTEMPTS):
        auth.register_failed_login(alvo_id)
    hash_antigo = _estado(raw, alvo_id)["password_hash"]

    app = FastAPI()
    app.include_router(users.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(
        user_id=contas["admin_id"], role="admin", source="session"
    )
    client = TestClient(app, raise_server_exceptions=False)

    resposta = client.post(f"/users/{alvo_id}/reset-password")
    assert resposta.status_code == 200, (db_backend, resposta.text)
    corpo = resposta.json()
    assert corpo["revoked_sessions"] == 1, db_backend

    linha = _estado(raw, alvo_id)
    assert linha["password_hash"] != hash_antigo, db_backend
    assert auth.verify_password(corpo["temporary_password"], linha["password_hash"]), db_backend
    assert linha["must_change_password"] == 1, db_backend
    assert linha["failed_login_attempts"] == 0, db_backend
    assert linha["locked_until"] is None, db_backend

    with raw.cursor() as cur:
        cur.execute("SELECT revoked_at FROM user_sessions WHERE token_hash = %s", (token_hash,))
        assert cur.fetchone()["revoked_at"] is not None, db_backend
