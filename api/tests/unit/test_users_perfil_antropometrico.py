"""Perfil antropométrico na escrita (nutrição L2a): `PUT /users/me`,
`PUT /users/{id}` (admin) e `POST /users` validam `sex`, `height_cm` e
`activity_level` pelo motor (`domain/nutrition/anthropometrics`), respondem 400
com o código do motor e gravam SEMPRE o código canônico; `GET /users(/me)`
devolve o código canônico também para valor antigo ainda não convertido.

Sem banco: `transaction` falso que registra os UPDATE/INSERT.
Rodar: cd api && python -m pytest tests/unit/test_users_perfil_antropometrico.py
"""

from __future__ import annotations

import re
from decimal import Decimal

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import users
from app.domain.nutrition import anthropometrics as anthro
from app.security import Identity, get_current_identity, require_admin
from tests.support.fake_db import fake_transaction

_LINHA = {
    "id": 7,
    "username": "u7",
    "password_hash": "x",
    "full_name": "U7",
    "role": "regular",
    "photo_path": None,
    "is_advanced_user": 0,
    "intervals_athlete_id": None,
    "intervals_api_key_encrypted": None,
    "sex": "M",
    "height_cm": Decimal("178.5"),
    "activity_level": "moderate",
}


class _Cursor:
    def __init__(self):
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
        if flat.startswith("SELECT id FROM users WHERE username"):
            self._result = None
        elif flat.startswith("SELECT *"):
            self._result = dict(_LINHA)
        else:
            self._result = {"id": 7}

    def fetchone(self):
        return self._result

    def fetchall(self):
        return [dict(_LINHA)]

    def escritas(self) -> list[tuple[str, tuple | None]]:
        return [(sql, p) for sql, p in self.executed if sql.startswith(("UPDATE users", "INSERT INTO users"))]


def _client(monkeypatch, role="regular"):
    cur = _Cursor()
    monkeypatch.setattr(users, "transaction", fake_transaction(cur))
    app = FastAPI()
    app.include_router(users.router)
    identity = Identity(user_id=7, role=role, source="session")
    app.dependency_overrides[get_current_identity] = lambda: identity
    app.dependency_overrides[require_admin] = lambda: identity
    return TestClient(app, raise_server_exceptions=False), cur


def _valor(sql: str, params: tuple, coluna: str):
    """Valor ligado a `<coluna> = COALESCE(%s, …)` ou `<coluna> = %s` no UPDATE (um `%s` por coluna)."""
    achado = re.search(rf"\b{coluna} = (?:COALESCE\()?%s", sql)
    assert achado, f"{coluna} fora do UPDATE: {sql}"
    return params[sql[: achado.start()].count("%s")]


# ---------------------------------------------------------------------------
# PUT /users/me e PUT /users/{id}
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("rota", "role"), [("/users/me", "regular"), ("/users/7", "admin")])
def test_grava_o_codigo_canonico_e_a_altura(monkeypatch, rota, role):
    client, cur = _client(monkeypatch, role=role)
    resp = client.put(rota, json={"sex": "F", "height_cm": 165.5, "activity_level": "Muito ativo"})
    assert resp.status_code == 200, resp.text
    [(sql, params)] = cur.escritas()
    assert _valor(sql, params, "sex") == "female"
    assert _valor(sql, params, "height_cm") == 165.5
    assert _valor(sql, params, "activity_level") == "very_active"
    assert params[-1] == 7 and sql.count("%s") == len(params)


@pytest.mark.parametrize(("rota", "role"), [("/users/me", "regular"), ("/users/7", "admin")])
def test_campo_ausente_ou_vazio_nao_altera(monkeypatch, rota, role):
    client, cur = _client(monkeypatch, role=role)
    resp = client.put(rota, json={"full_name": "Novo", "sex": "", "height_cm": None, "activity_level": "  "})
    assert resp.status_code == 200, resp.text
    [(sql, params)] = cur.escritas()
    assert _valor(sql, params, "sex") is None  # COALESCE preserva o gravado
    # Colunas da 074 só entram no SET quando preenchidas: salvar o resto do
    # perfil não depende da 074 aplicada (revisão de impacto em produção).
    assert "height_cm" not in sql and "activity_level" not in sql
    assert sql.count("%s") == len(params)


def test_trecho_da_074_so_com_o_que_veio():
    assert users._anthropometric_set(None, None) == ("", ())
    assert users._anthropometric_set(170.0, None) == (", height_cm = %s", (170.0,))
    assert users._anthropometric_set(None, "light") == (", activity_level = %s", ("light",))
    assert users._anthropometric_set(170.0, "light") == (", height_cm = %s, activity_level = %s", (170.0, "light"))


@pytest.mark.parametrize(
    ("corpo", "codigo"),
    [
        ({"sex": "abc"}, anthro.INVALID_SEX),
        ({"activity_level": "atleta"}, anthro.INVALID_ACTIVITY_LEVEL),
        ({"height_cm": anthro.HEIGHT_CM_RANGE[0] - 1}, anthro.INVALID_HEIGHT),
        ({"height_cm": anthro.HEIGHT_CM_RANGE[1] + 1}, anthro.INVALID_HEIGHT),
        ({"height_cm": 0}, anthro.INVALID_HEIGHT),
    ],
)
@pytest.mark.parametrize(("rota", "role"), [("/users/me", "regular"), ("/users/7", "admin")])
def test_valor_fora_do_motor_e_400_com_o_codigo_e_nada_gravado(monkeypatch, rota, role, corpo, codigo):
    client, cur = _client(monkeypatch, role=role)
    resp = client.put(rota, json=corpo)
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["error"] == codigo
    assert resp.json()["detail"]["message"]
    assert cur.escritas() == []


def test_mensagem_interpola_a_faixa_e_os_codigos_do_motor():
    with pytest.raises(HTTPException) as exc:
        users._validate_height_cm(10)
    low, high = anthro.HEIGHT_CM_RANGE
    assert f"{low:g}" in exc.value.detail["message"] and f"{high:g}" in exc.value.detail["message"]
    with pytest.raises(HTTPException) as exc:
        users._validate_sex("abc")
    assert all(sexo.value in exc.value.detail["message"] for sexo in anthro.Sex)


def test_altura_nos_limites_da_faixa_e_aceita():
    low, high = anthro.HEIGHT_CM_RANGE
    assert users._validate_height_cm(low) == low
    assert users._validate_height_cm(high) == high


def test_tipo_errado_continua_422_do_modelo(monkeypatch):
    client, cur = _client(monkeypatch)
    assert client.put("/users/me", json={"height_cm": "alto"}).status_code == 422
    assert client.put("/users/me", json={"activity_level": 3}).status_code == 422
    assert cur.escritas() == []


# ---------------------------------------------------------------------------
# POST /users (admin) e leitura
# ---------------------------------------------------------------------------


def test_criacao_pelo_admin_grava_o_codigo_canonico(monkeypatch):
    client, cur = _client(monkeypatch, role="admin")
    resp = client.post("/users", json={"username": "maria", "password": "Senha12345", "sex": "Mulher"})
    assert resp.status_code == 200, resp.text
    [(sql, params)] = cur.escritas()
    assert sql.startswith("INSERT INTO users") and "female" in params and "Mulher" not in params
    resp = client.post("/users", json={"username": "x", "password": "Senha12345", "sex": "abc"})
    assert resp.status_code == 400 and resp.json()["detail"]["error"] == anthro.INVALID_SEX


def test_leitura_devolve_codigo_canonico_mesmo_com_valor_antigo(monkeypatch):
    client, _cur = _client(monkeypatch, role="admin")
    item = client.get("/users/me").json()["item"]
    assert item["sex"] == "male" and item["activity_level"] == "moderate" and item["height_cm"] == 178.5
    [item] = client.get("/users").json()["items"]
    assert item["sex"] == "male"


def test_public_row_linha_anterior_a_074_ou_valor_desconhecido_vira_null():
    linha = {k: v for k, v in _LINHA.items() if k not in ("height_cm", "activity_level")}
    out = users._public_row({**linha, "sex": "abc"})
    assert out["sex"] is None and out["height_cm"] is None and out["activity_level"] is None
