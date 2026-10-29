"""SEG-01: `photo_path` nao vem mais do body e a leitura/exclusao da foto de
perfil so aceita a chave canonica `users/{id}.{ext}` do proprio usuario.

Rodar: cd api && python -m pytest tests/unit/test_users_photo_security.py
"""

from __future__ import annotations


import pytest
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

from app import users
from app.security import Identity, get_current_identity
from tests.support.fake_db import fake_transaction


class _Cursor:
    def __init__(self, photo_path):
        self.photo_path = photo_path
        self.executed: list[tuple[str, tuple | None]] = []
        self.lastrowid = 7
        self._result = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.executed.append((flat, params))
        if flat.startswith("SELECT photo_path"):
            self._result = {"photo_path": self.photo_path}
        elif flat.startswith("SELECT *"):
            self._result = {"id": 7, "username": "u7", "photo_path": self.photo_path}
        elif flat.startswith("SELECT id FROM users WHERE username"):
            self._result = None
        else:
            self._result = {"id": 7}

    def fetchone(self):
        return self._result


def _make_client(monkeypatch, photo_path=None, user_id=7, role="regular"):
    cur = _Cursor(photo_path)

    monkeypatch.setattr(users, "transaction", fake_transaction(cur))
    s3_calls = {"read": [], "delete": []}

    def fake_read(key):
        s3_calls["read"].append(key)
        return Response(content=b"img", media_type="image/jpeg")

    monkeypatch.setattr(users, "_read_photo", fake_read)
    monkeypatch.setattr(users, "_delete_photo", lambda key: s3_calls["delete"].append(key))

    app = FastAPI()
    app.include_router(users.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=user_id, role=role, source="session")
    return TestClient(app, raise_server_exceptions=False), cur, s3_calls


# (a) photo_path do body nunca chega ao banco


def test_put_me_ignora_photo_path_do_body(monkeypatch):
    client, cur, _ = _make_client(monkeypatch)
    # SEG-11: `photo_path` nao e campo do modelo (extra="forbid"): 422 e nada gravado.
    resp = client.put("/users/me", json={"full_name": "Novo Nome", "photo_path": "users/2.jpg"})
    assert resp.status_code == 422
    assert not [1 for sql, _p in cur.executed if sql.startswith("UPDATE users")]
    resp = client.put("/users/me", json={"full_name": "Novo Nome"})
    assert resp.status_code == 200
    updates = [(sql, p) for sql, p in cur.executed if sql.startswith("UPDATE users")]
    assert updates, "esperava um UPDATE users"
    for sql, params in updates:
        assert "photo_path" not in sql
    assert "Novo Nome" in updates[0][1]


def test_put_admin_ignora_photo_path_do_body(monkeypatch):
    client, cur, _ = _make_client(monkeypatch, role="admin", user_id=1)
    resp = client.put("/users/7", json={"full_name": "X", "photo_path": "reports/x.pdf"})
    assert resp.status_code == 422
    assert not [1 for sql, _p in cur.executed if sql.startswith("UPDATE users")]
    resp = client.put("/users/7", json={"full_name": "X"})
    assert resp.status_code == 200
    updates = [(sql, p) for sql, p in cur.executed if sql.startswith("UPDATE users")]
    assert updates
    for sql, params in updates:
        assert "photo_path" not in sql


def test_criacao_ignora_photo_path_do_body(monkeypatch):
    monkeypatch.setattr(users, "hash_password", lambda p: "hash")
    client, cur, _ = _make_client(monkeypatch, role="admin", user_id=1)
    resp = client.post(
        "/users", json={"username": "novo", "password": "senha-bem-longa-123", "photo_path": "users/2.jpg"}
    )
    assert resp.status_code == 422
    assert not [1 for sql, _p in cur.executed if sql.startswith("INSERT INTO users")]
    resp = client.post("/users", json={"username": "novo", "password": "senha-bem-longa-123"})
    assert resp.status_code == 200
    inserts = [(sql, p) for sql, p in cur.executed if sql.startswith("INSERT INTO users")]
    assert inserts
    assert "photo_path" not in inserts[0][0]


# (b) helper canonico


@pytest.mark.parametrize("key", ["users/7.jpg", "users/7.jpeg", "users/7.png", "users/7.webp", "users/7.gif"])
def test_helper_aceita_chave_canonica_do_proprio_usuario(key):
    assert users._user_photo_key_is_canonical(7, key) is True


@pytest.mark.parametrize(
    "key",
    [
        "users/8.jpg",
        "users/77.jpg",
        "users/17.jpg",
        "reports/x.pdf",
        "users/7.jpg/../x",
        "../users/7.jpg",
        "users/7.jpg\n",
        "users/7.svg",
        "exercise-card-drafts/a.png",
        "equipment/7.png",
        "",
        None,
        123,
    ],
)
def test_helper_rejeita_chave_nao_canonica(key):
    assert users._user_photo_key_is_canonical(7, key) is False


# (c) rotas de foto


def test_get_foto_nao_canonica_da_404_e_nao_toca_o_s3(monkeypatch):
    client, _, s3 = _make_client(monkeypatch, photo_path="users/2.jpg")
    resp = client.get("/users/7/photo")
    assert resp.status_code == 404
    assert s3["read"] == []


def test_get_foto_canonica_le_a_chave_certa(monkeypatch):
    client, _, s3 = _make_client(monkeypatch, photo_path="users/7.jpg")
    resp = client.get("/users/7/photo")
    assert resp.status_code == 200
    assert s3["read"] == ["users/7.jpg"]


def test_get_foto_de_outro_usuario_por_regular_continua_403(monkeypatch):
    client, _, s3 = _make_client(monkeypatch, photo_path="users/8.jpg")
    assert client.get("/users/8/photo").status_code == 403
    assert s3["read"] == []


def test_delete_foto_canonica_apaga_objeto_e_limpa_coluna(monkeypatch):
    client, cur, s3 = _make_client(monkeypatch, photo_path="users/7.png")
    resp = client.delete("/users/7/photo")
    assert resp.status_code == 200
    assert s3["delete"] == ["users/7.png"]
    assert any(sql.startswith("UPDATE users SET photo_path = NULL") for sql, _ in cur.executed)


def test_delete_foto_nao_canonica_nunca_apaga_o_objeto_mas_limpa_a_coluna(monkeypatch):
    client, cur, s3 = _make_client(monkeypatch, photo_path="equipment/3.png")
    resp = client.delete("/users/7/photo")
    assert resp.status_code == 200
    assert s3["delete"] == []
    assert any(sql.startswith("UPDATE users SET photo_path = NULL") for sql, _ in cur.executed)
