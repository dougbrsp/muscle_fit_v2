"""Troca de senha (`POST /auth/change-password`) nunca responde 401.

Bug de producao (2026-10-10): a senha atual errada respondia 401; o
`apiFetch` do frontend trata todo 401 como sessao expirada e redirecionava -
o usuario da troca obrigatoria nunca via "Senha atual incorreta" e ficava em
loop. 401 fica so para sessao invalida; aqui:
- senha atual errada -> 400 `{"detail", "code": "wrong_current_password"}`,
  contando no lockout (`register_failed_login`);
- tentativa que bloqueia, ou conta ja bloqueada -> 429 `code: account_locked`;
- senha certa -> zera `must_change_password` e revoga as OUTRAS sessoes.

Rodar: cd api && python -m pytest tests/unit/test_troca_senha_sem_401.py
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import auth
from app.security import Identity, get_current_identity
from tests.support.fake_db import NullContext, fake_transaction

SENHA = "SenhaTemporaria1!"
NOVA = "SenhaNovaDoUsuario9"
TOKEN = "token-da-sessao-atual"
USER_ID = 7


class _Cursor(NullContext):
    """Cursor roteirizado: devolve o usuario no SELECT e guarda os UPDATEs."""

    def __init__(self, locked_until=None):
        self.user = {"id": USER_ID, "password_hash": auth.hash_password(SENHA), "locked_until": locked_until}
        self.executed: list[tuple[str, tuple]] = []
        self._last = None

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))
        self._last = self.user if sql.lstrip().upper().startswith("SELECT") else None

    def fetchone(self):
        return self._last

    def updates(self):
        return [(sql, params) for sql, params in self.executed if sql.upper().startswith("UPDATE")]


@pytest.fixture
def falhas(monkeypatch):
    """Substitui `register_failed_login`: registra a chamada e devolve se
    bloqueou (`bloqueia`), como a funcao real."""
    estado = {"chamadas": [], "bloqueia": False}

    def _registrar(user_id):
        estado["chamadas"].append(user_id)
        return estado["bloqueia"]

    monkeypatch.setattr(auth, "register_failed_login", _registrar)
    return estado


def _client(monkeypatch, cur) -> TestClient:
    monkeypatch.setattr(auth, "transaction", fake_transaction(cur))
    app = FastAPI()
    app.include_router(auth.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(
        user_id=USER_ID, role="regular", source="session", must_change_password=True
    )
    return TestClient(app, raise_server_exceptions=False, headers={"X-API-Token": TOKEN})


def _trocar(client, atual):
    return client.post("/auth/change-password", json={"current_password": atual, "new_password": NOVA})


def test_senha_atual_errada_responde_400_com_code_e_conta_no_lockout(monkeypatch, falhas):
    cur = _Cursor()
    resp = _trocar(_client(monkeypatch, cur), "senha-errada-123")

    assert resp.status_code == 400
    assert resp.json() == {"detail": "Senha atual incorreta.", "code": "wrong_current_password"}
    assert falhas["chamadas"] == [USER_ID]
    assert cur.updates() == []  # nada trocado, nenhuma sessao revogada


def test_tentativa_que_bloqueia_responde_429_claro(monkeypatch, falhas):
    falhas["bloqueia"] = True
    resp = _trocar(_client(monkeypatch, _Cursor()), "senha-errada-123")

    assert resp.status_code == 429
    assert resp.json() == {"detail": auth.ACCOUNT_LOCKED_DETAIL, "code": "account_locked"}
    assert 0 < int(resp.headers["Retry-After"]) <= auth.LOCKOUT_MINUTES * 60
    assert falhas["chamadas"] == [USER_ID]


def test_conta_ja_bloqueada_responde_429_sem_conferir_a_senha(monkeypatch, falhas):
    cur = _Cursor(locked_until=datetime.utcnow() + timedelta(minutes=5))
    resp = _trocar(_client(monkeypatch, cur), SENHA)  # nem a senha certa passa

    assert resp.status_code == 429
    assert resp.json()["code"] == "account_locked"
    assert falhas["chamadas"] == []
    assert cur.updates() == []


def test_bloqueio_expirado_nao_impede_a_troca(monkeypatch, falhas):
    cur = _Cursor(locked_until=datetime.utcnow() - timedelta(minutes=1))
    assert _trocar(_client(monkeypatch, cur), SENHA).status_code == 200


def test_senha_certa_zera_must_change_e_revoga_as_outras_sessoes(monkeypatch, falhas):
    cur = _Cursor()
    resp = _trocar(_client(monkeypatch, cur), SENHA)

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    assert falhas["chamadas"] == []

    (troca_sql, troca_params), (revoga_sql, revoga_params) = cur.updates()
    assert troca_sql.startswith("UPDATE users SET password_hash")
    assert "must_change_password = 0" in troca_sql
    assert auth.verify_password(NOVA, troca_params[0])
    assert troca_params[1] == USER_ID
    assert revoga_sql.startswith("UPDATE user_sessions SET revoked_at")
    assert "token_hash <> %s" in revoga_sql  # a sessao atual sobrevive
    assert revoga_params == (USER_ID, auth._hash_token(TOKEN))


def test_nenhum_erro_da_troca_e_401(monkeypatch, falhas):
    for bloqueia in (False, True):
        falhas["bloqueia"] = bloqueia
        assert _trocar(_client(monkeypatch, _Cursor()), "senha-errada-123").status_code != 401
