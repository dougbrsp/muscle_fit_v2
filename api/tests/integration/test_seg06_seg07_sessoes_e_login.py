"""SEG-06 (sessoes / troca obrigatoria) e SEG-07 (login) contra o MySQL de teste.

As consultas sao relidas em transacao propria: o que importa e o que foi
commitado, nao a resposta HTTP.
"""

from __future__ import annotations

from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import auth, gyms, login_throttle, users, workout_plans
from app.auth import MAX_FAILED_ATTEMPTS, hash_password
from app.db import transaction
from app.security import MustChangePasswordRequired, must_change_password_handler

SENHA = "SenhaOriginal123"
NOVA = "OutraSenha4567"


@pytest.fixture(autouse=True)
def cookie_sem_dominio(monkeypatch):
    monkeypatch.setenv("SESSION_COOKIE_DOMAIN", "")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "0")


@pytest.fixture
def contas(catalog):
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM user_sessions")
            cur.execute("DELETE FROM password_reset_attempts")
            cur.execute(
                "INSERT INTO users (username, password_hash, role, is_active) VALUES (%s, %s, 'regular', 1)",
                ("alvo", hash_password(SENHA)),
            )
            alvo_id = cur.lastrowid
    return {"alvo_id": alvo_id}


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(auth.router)
    app.include_router(users.router)
    app.include_router(workout_plans.router)
    app.include_router(gyms.router)  # resolve_identity_from_credentials (SEG-06)
    app.add_exception_handler(MustChangePasswordRequired, must_change_password_handler)
    return app


def _client(peer_xff: str | None = None) -> TestClient:
    headers = {"X-Forwarded-For": peer_xff} if peer_xff else {}
    # `testclient` nao e IP local: o XFF so conta quando o peer e 127.0.0.1.
    return TestClient(_app(), raise_server_exceptions=False, headers=headers)


def _login(client: TestClient, username="alvo", password=SENHA):
    return client.post("/auth/login", json={"username": username, "password": password})


# ------------------------------------------------------------------ SEG-06


def test_trocar_senha_revoga_as_outras_sessoes_e_mantem_a_atual(contas):
    a, b = _client(), _client()
    assert _login(a).status_code == 200
    assert _login(b).status_code == 200
    assert a.get("/auth/me").status_code == 200
    assert b.get("/auth/me").status_code == 200

    r = a.post("/auth/change-password", json={"current_password": SENHA, "new_password": NOVA})
    assert r.status_code == 200

    assert a.get("/auth/me").status_code == 200  # a atual segue valida
    assert b.get("/auth/me").status_code == 401  # a outra foi revogada


def test_senha_atual_errada_na_troca_conta_no_lockout(contas):
    c = _client()
    assert _login(c).status_code == 200
    errada = {"current_password": "errada-errada", "new_password": NOVA}
    # Nunca 401 (o frontend trataria como sessao expirada): 400 com code.
    for _ in range(MAX_FAILED_ATTEMPTS - 1):
        r = c.post("/auth/change-password", json=errada)
        assert r.status_code == 400
        assert r.json() == {"detail": auth.WRONG_CURRENT_PASSWORD_DETAIL, "code": auth.WRONG_CURRENT_PASSWORD_CODE}
    # a tentativa que bloqueia ja responde 429 "conta bloqueada".
    r = c.post("/auth/change-password", json=errada)
    assert r.status_code == 429
    assert r.json()["code"] == auth.ACCOUNT_LOCKED_CODE
    assert int(r.headers["Retry-After"]) > 0
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT locked_until FROM users WHERE id = %s", (contas["alvo_id"],))
            assert cur.fetchone()["locked_until"] is not None
    # bloqueada: nem a senha atual certa troca (o lockout vale aqui tambem)...
    r = c.post("/auth/change-password", json={"current_password": SENHA, "new_password": NOVA})
    assert r.status_code == 429
    # ...a sessao segue valida...
    assert c.get("/auth/me").status_code == 200
    # ...e login novo (mesmo com a senha certa) falha igual.
    assert _login(_client()).status_code == 401


def test_must_change_password_e_imposto_na_api(contas):
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE users SET must_change_password = 1 WHERE id = %s", (contas["alvo_id"],))
    c = _client()
    assert _login(c).status_code == 200

    r = c.get("/workout-plans")
    assert r.status_code == 403
    assert r.json()["code"] == "must_change_password"
    assert isinstance(r.json()["detail"], str)
    # Rotas com identidade por header/cookie direto (catalogo, gyms...) tambem.
    r = c.get("/gyms")
    assert r.status_code == 403
    assert r.json()["code"] == "must_change_password"

    assert c.get("/auth/me").status_code == 200
    assert c.get("/users/me").status_code == 200

    assert c.post("/auth/change-password", json={"current_password": SENHA, "new_password": NOVA}).status_code == 200
    assert c.get("/workout-plans").status_code == 200
    assert c.get("/gyms").status_code == 200


# ------------------------------------------------------------------ SEG-07


def test_login_correto_200(contas):
    assert _login(_client()).status_code == 200


def test_inexistente_e_senha_errada_sao_indistinguiveis(contas):
    errada = _login(_client(), "alvo", "senha-errada")
    inexistente = _login(_client(), "ninguem", "senha-errada")
    assert errada.status_code == inexistente.status_code == 401
    assert errada.json() == inexistente.json()


def test_bcrypt_roda_nos_dois_caminhos(contas):
    with mock.patch.object(auth, "verify_password", wraps=auth.verify_password) as spy:
        _login(_client(), "alvo", "senha-errada")
        assert spy.call_count == 1
        _login(_client(), "ninguem", "senha-errada")
        assert spy.call_count == 2


def test_conta_bloqueada_responde_igual_a_credencial_invalida(contas):
    for _ in range(MAX_FAILED_ATTEMPTS):
        _login(_client(), "alvo", "senha-errada")
    bloqueada = _login(_client(), "alvo", SENHA)
    inexistente = _login(_client(), "ninguem", SENHA)
    assert bloqueada.status_code == inexistente.status_code == 401
    assert bloqueada.json() == inexistente.json()


def test_vinte_e_uma_falhas_do_mesmo_ip_em_contas_diferentes_viram_429(contas):
    c = _client()
    for i in range(login_throttle.MAX_FAILURES_PER_IP):
        assert _login(c, f"conta-{i}", "x").status_code == 401
    assert _login(c, "conta-final", "x").status_code == 429
    # o limite e por IP e por FALHA: outro IP (via XFF so vale atras do adapter) nao e afetado aqui,
    # mas o login correto tambem fica barrado enquanto o IP esta no limite.
    assert _login(c, "alvo", SENHA).status_code == 429


def test_login_correto_nao_consome_o_limite_por_ip(contas):
    c = _client()
    for _ in range(login_throttle.MAX_FAILURES_PER_IP + 5):
        assert _login(c, "alvo", SENHA).status_code == 200
