"""Dividas tecnicas de autenticacao (fora do plano de refatoracao dos motores).

Divida 1 - bloqueio por tentativas de login inoperante: o incremento de
`failed_login_attempts` era escrito DENTRO do `with transaction()` do login e
o endpoint levantava `HTTPException` logo em seguida; o context manager dava
ROLLBACK e descartava a escrita. Por isso estes testes NAO se contentam com o
status da resposta - eles RELEEM a linha do banco numa conexao nova. Um teste
que so olhasse o 401/423 passaria mesmo com o bug.

Divida 2 - reset administrativo de senha (POST /users/{id}/reset-password):
antes dele, um usuario sem acesso so voltava com UPDATE manual no banco.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import auth, users
from app.auth import MAX_FAILED_ATTEMPTS, hash_password
from app.db import transaction
from app.security import Identity, get_current_identity

SENHA_ALVO = "SenhaOriginal123"


@pytest.fixture(autouse=True)
def cookie_sem_dominio(monkeypatch):
    """TestClient roda em `http://testserver`: o dominio default do cookie de
    sessao (`.musclefit.com.br`) faria o cliente descartar o Set-Cookie."""
    monkeypatch.setenv("SESSION_COOKIE_DOMAIN", "")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "0")


@pytest.fixture
def contas(catalog):
    """`catalog` ja truncou `users` e criou o admin de teste; aqui entram o
    usuario alvo (com senha real) e a limpeza das sessoes."""
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM user_sessions")
            cur.execute("DELETE FROM password_reset_attempts")  # SEG-07: limite de falhas por IP
            cur.execute(
                "INSERT INTO users (username, password_hash, role, is_active) VALUES (%s, %s, 'regular', 1)",
                ("alvo", hash_password(SENHA_ALVO)),
            )
            alvo_id = cur.lastrowid

    return {"admin_id": catalog.user_id, "alvo_id": alvo_id}


@pytest.fixture
def client_publico(contas) -> TestClient:
    """App sem override de identidade: exercita o login de verdade."""
    app = FastAPI()
    app.include_router(auth.router)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def client_admin(contas) -> TestClient:
    app = FastAPI()
    app.include_router(users.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(
        user_id=contas["admin_id"], role="admin", source="session"
    )
    return TestClient(app, raise_server_exceptions=False)


def ler_usuario(user_id: int) -> dict:
    """Le a linha numa transacao PROPRIA - e o que prova a durabilidade."""
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT failed_login_attempts, locked_until, must_change_password FROM users WHERE id = %s",
                (user_id,),
            )
            return cur.fetchone()


def tentativa_errada(client: TestClient):
    return client.post("/auth/login", json={"username": "alvo", "password": "senha-errada"})


def tentativa_certa(client: TestClient):
    return client.post("/auth/login", json={"username": "alvo", "password": SENHA_ALVO})


# ---------------------------------------------------------------------------
# Divida 1 - lockout
# ---------------------------------------------------------------------------


def test_tentativas_erradas_sao_persistidas_e_nao_bloqueiam_antes_do_limite(client_publico, contas):
    for esperado in range(1, MAX_FAILED_ATTEMPTS):
        resposta = tentativa_errada(client_publico)
        assert resposta.status_code == 401
        assert resposta.json()["detail"] == auth.INVALID_CREDENTIALS_DETAIL

        linha = ler_usuario(contas["alvo_id"])
        assert linha["failed_login_attempts"] == esperado, "contador nao sobreviveu ao rollback do erro"
        assert linha["locked_until"] is None


def test_enesima_tentativa_bloqueia_e_persiste_locked_until(client_publico, contas):
    for _ in range(MAX_FAILED_ATTEMPTS - 1):
        assert tentativa_errada(client_publico).status_code == 401

    resposta = tentativa_errada(client_publico)
    # SEG-07: a resposta do bloqueio e a MESMA da credencial invalida (sem 423).
    assert resposta.status_code == 401
    assert resposta.json()["detail"] == auth.INVALID_CREDENTIALS_DETAIL

    linha = ler_usuario(contas["alvo_id"])
    assert linha["locked_until"] is not None, "locked_until nao foi gravado"
    assert linha["locked_until"] > datetime.utcnow()
    # Semantica preservada: zera o contador junto com o bloqueio, para que ao
    # expirar o lockout o usuario volte com as tentativas cheias.
    assert linha["failed_login_attempts"] == 0


def test_durante_o_lockout_ate_a_senha_certa_recebe_401_generico(client_publico, contas):
    for _ in range(MAX_FAILED_ATTEMPTS):
        tentativa_errada(client_publico)

    resposta = tentativa_certa(client_publico)
    assert resposta.status_code == 401
    assert resposta.json()["detail"] == auth.INVALID_CREDENTIALS_DETAIL


def test_apos_expirar_o_lockout_o_acesso_volta_com_contador_zerado(client_publico, contas):
    for _ in range(MAX_FAILED_ATTEMPTS):
        tentativa_errada(client_publico)

    # Envelhece o bloqueio em vez de esperar 15 minutos reais.
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET locked_until = %s WHERE id = %s",
                (datetime.utcnow() - timedelta(minutes=1), contas["alvo_id"]),
            )

    assert tentativa_certa(client_publico).status_code == 200

    linha = ler_usuario(contas["alvo_id"])
    assert linha["failed_login_attempts"] == 0
    assert linha["locked_until"] is None


def test_login_correto_no_meio_do_caminho_zera_o_contador(client_publico, contas):
    for _ in range(MAX_FAILED_ATTEMPTS - 2):
        tentativa_errada(client_publico)
    assert ler_usuario(contas["alvo_id"])["failed_login_attempts"] == MAX_FAILED_ATTEMPTS - 2

    assert tentativa_certa(client_publico).status_code == 200
    assert ler_usuario(contas["alvo_id"])["failed_login_attempts"] == 0

    # E o limite volta a valer do zero: mais MAX-1 erros ainda nao bloqueiam.
    for _ in range(MAX_FAILED_ATTEMPTS - 1):
        assert tentativa_errada(client_publico).status_code == 401
    assert ler_usuario(contas["alvo_id"])["locked_until"] is None


# ---------------------------------------------------------------------------
# Divida 2 - reset administrativo
# ---------------------------------------------------------------------------


def test_admin_reseta_e_a_senha_devolvida_autentica(client_admin, client_publico, contas):
    resposta = client_admin.post(f"/users/{contas['alvo_id']}/reset-password")
    assert resposta.status_code == 200, resposta.text
    corpo = resposta.json()

    nova_senha = corpo["temporary_password"]
    assert nova_senha and nova_senha != SENHA_ALVO
    assert corpo["must_change_password"] is True

    # A senha antiga nao vale mais; a nova vale.
    assert tentativa_certa(client_publico).status_code == 401
    login = client_publico.post("/auth/login", json={"username": "alvo", "password": nova_senha})
    assert login.status_code == 200, login.text
    assert login.json()["user"]["must_change_password"] is True

    assert ler_usuario(contas["alvo_id"])["must_change_password"] == 1


def test_reset_derruba_as_sessoes_ativas(client_admin, client_publico, contas):
    assert tentativa_certa(client_publico).status_code == 200
    # A sessao aberta funciona antes do reset.
    assert client_publico.get("/auth/me").status_code == 200

    resposta = client_admin.post(f"/users/{contas['alvo_id']}/reset-password")
    assert resposta.status_code == 200
    assert resposta.json()["revoked_sessions"] >= 1

    # Mesmo cookie, agora revogado.
    assert client_publico.get("/auth/me").status_code == 401


def test_reset_limpa_bloqueio_e_contador(client_admin, client_publico, contas):
    for _ in range(MAX_FAILED_ATTEMPTS):
        tentativa_errada(client_publico)
    assert ler_usuario(contas["alvo_id"])["locked_until"] is not None

    assert client_admin.post(f"/users/{contas['alvo_id']}/reset-password").status_code == 200

    linha = ler_usuario(contas["alvo_id"])
    assert linha["locked_until"] is None
    assert linha["failed_login_attempts"] == 0


def test_nao_admin_recebe_403(contas):
    app = FastAPI()
    app.include_router(users.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(
        user_id=contas["alvo_id"], role="regular", source="session"
    )
    client = TestClient(app, raise_server_exceptions=False)

    resposta = client.post(f"/users/{contas['admin_id']}/reset-password")
    assert resposta.status_code == 403


def test_usuario_inexistente_recebe_404(client_admin):
    assert client_admin.post("/users/99999999/reset-password").status_code == 404


def test_admin_nao_pode_resetar_a_si_mesmo(client_admin, contas):
    resposta = client_admin.post(f"/users/{contas['admin_id']}/reset-password")
    assert resposta.status_code == 400
    assert "senha" in resposta.json()["detail"].lower()


def test_resposta_do_reset_nao_vaza_hash_nem_a_senha_em_log(client_admin, contas, caplog):
    with caplog.at_level("INFO", logger="musclefit.events"):
        resposta = client_admin.post(f"/users/{contas['alvo_id']}/reset-password")

    corpo = resposta.json()
    assert "password_hash" not in corpo

    eventos = [registro.getMessage() for registro in caplog.records]
    assert any('"event": "admin.password_reset"' in mensagem for mensagem in eventos)
    assert all(corpo["temporary_password"] not in mensagem for mensagem in eventos)
