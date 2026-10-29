"""FCmáx manual no perfil e zonas de FC resolvidas (M98 C1).

- `PUT /users/me` e `PUT /users/{id}`: `max_hr_bpm_manual` validado pelo dono
  (`hr_zones.validate_manual_max_hr`), 400 fora da faixa; ausente = não toca a
  coluna (o resto do perfil salva sem depender da 096); `null` explícito limpa.
- `GET /users/me/hr-zones`: precedência do dono (sem Intervals na C1) -> FCmáx
  manual -> sem zonas; linha sem a coluna (antes da 096) = sem zonas.

Sem banco: `transaction` falso que registra os UPDATE.
Rodar: cd api && python -m pytest tests/unit/test_users_fcmax_zonas.py
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import users
from app.domain import hr_zones
from app.security import Identity, get_current_identity, require_admin
from tests.support.fake_db import fake_transaction

_BASE = {
    "id": 7,
    "username": "u7",
    "password_hash": "x",
    "full_name": "U7",
    "role": "regular",
    "photo_path": None,
    "is_advanced_user": 0,
    "intervals_athlete_id": None,
    "intervals_api_key_encrypted": None,
}


class _Cursor:
    def __init__(self, linha):
        self.linha = linha
        self.executed: list[tuple[str, tuple | None]] = []
        self._result = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.executed.append((flat, params))
        self._result = dict(self.linha) if flat.startswith("SELECT *") else {"id": 7}

    def fetchone(self):
        return self._result

    def fetchall(self):
        return [dict(self.linha)]

    def update(self) -> tuple[str, tuple]:
        (sql, params), = [(s, p) for s, p in self.executed if s.startswith("UPDATE users")]
        return sql, params


def _client(monkeypatch, linha=None, role="regular"):
    cur = _Cursor(linha if linha is not None else _BASE)
    monkeypatch.setattr(users, "transaction", fake_transaction(cur))
    app = FastAPI()
    app.include_router(users.router)
    identity = Identity(user_id=7, role=role, source="session")
    app.dependency_overrides[get_current_identity] = lambda: identity
    app.dependency_overrides[require_admin] = lambda: identity
    return TestClient(app), cur


@pytest.mark.parametrize("rota", ["/users/me", "/users/7"])
def test_fcmax_valida_grava_inteiro(monkeypatch, rota):
    client, cur = _client(monkeypatch, role="admin")
    resp = client.put(rota, json={"max_hr_bpm_manual": 185})
    assert resp.status_code == 200, resp.text
    sql, params = cur.update()
    assert "max_hr_bpm_manual = %s" in sql
    assert 185 in params


@pytest.mark.parametrize(
    "valor", [hr_zones.MANUAL_MAX_HR_MIN_BPM - 1, hr_zones.MANUAL_MAX_HR_MAX_BPM + 1, 180.5, "abc", True]
)
def test_fcmax_fora_da_regra_do_dono_e_400(monkeypatch, valor):
    client, cur = _client(monkeypatch)
    resp = client.put("/users/me", json={"max_hr_bpm_manual": valor})
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert detail["error"] == "invalid_max_hr_bpm_manual"
    assert str(hr_zones.MANUAL_MAX_HR_MIN_BPM) in detail["message"]
    assert not [s for s, _ in cur.executed if s.startswith("UPDATE users")]


def test_fcmax_ausente_nao_toca_a_coluna(monkeypatch):
    client, cur = _client(monkeypatch)
    assert client.put("/users/me", json={"full_name": "Novo"}).status_code == 200
    sql, _params = cur.update()
    assert "max_hr_bpm_manual" not in sql


def test_fcmax_null_explicito_limpa(monkeypatch):
    client, cur = _client(monkeypatch)
    assert client.put("/users/me", json={"max_hr_bpm_manual": None}).status_code == 200
    sql, params = cur.update()
    assert "max_hr_bpm_manual = %s" in sql
    assert params[-2] is None  # antes do id


def test_get_me_expoe_a_fcmax(monkeypatch):
    client, _ = _client(monkeypatch, linha={**_BASE, "max_hr_bpm_manual": 190})
    assert client.get("/users/me").json()["item"]["max_hr_bpm_manual"] == 190
    client, _ = _client(monkeypatch)  # linha anterior à 096
    assert client.get("/users/me").json()["item"]["max_hr_bpm_manual"] is None


def test_zonas_pela_fcmax_manual(monkeypatch):
    client, _ = _client(monkeypatch, linha={**_BASE, "max_hr_bpm_manual": 190})
    body = client.get("/users/me/hr-zones").json()
    esperado = hr_zones.athlete_zones_payload(hr_zones.resolve_athlete_zones(manual_max_hr_bpm=190))
    assert body == esperado
    assert body["source"] == "manual_max_hr"
    assert body["max_hr_bpm"] == 190
    assert [z["label"] for z in body["zones"]] == ["Z1", "Z2", "Z3", "Z4", "Z5"]
    assert body["zones"][-1]["upper_bpm"] == 190


@pytest.mark.parametrize("linha", [_BASE, {**_BASE, "max_hr_bpm_manual": None}, {**_BASE, "max_hr_bpm_manual": 40}])
def test_sem_fcmax_valida_sem_zonas(monkeypatch, linha):
    client, _ = _client(monkeypatch, linha=linha)
    assert client.get("/users/me/hr-zones").json() == {"source": "none", "max_hr_bpm": None, "zones": []}


def test_zonas_exigem_sessao(monkeypatch):
    cur = _Cursor(_BASE)
    monkeypatch.setattr(users, "transaction", fake_transaction(cur))
    app = FastAPI()
    app.include_router(users.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=7, role="regular", source="device")
    assert TestClient(app).get("/users/me/hr-zones").status_code == 403
