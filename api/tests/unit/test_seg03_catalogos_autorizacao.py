"""SEG-03: `/gym-equipment` (POST/PUT/DELETE) exige dono da academia ou admin e
`PUT /analytics/muscle-group-body-map` exige admin. Leituras seguem abertas.

Rodar: cd api && python -m pytest tests/unit/test_seg03_catalogos_autorizacao.py
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import dashboard_modules, gyms, security, services
from app.security import Identity, get_current_identity

OWNER = Identity(user_id=10, role="regular", source="session")
OTHER = Identity(user_id=11, role="regular", source="session")
ADMIN = Identity(user_id=1, role="admin", source="session")
IDENTITIES = {"owner": OWNER, "other": OTHER, "admin": ADMIN}


class _Cursor:
    """Dubla as consultas de gyms.py: dono da academia, vinculo e equipamento."""

    def __init__(self):
        self.executed: list[tuple[str, tuple | None]] = []
        self.rowcount = 1
        self._result = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.executed.append((flat, params))
        if flat.startswith("SELECT owner_user_id FROM gyms"):
            self._result = {"owner_user_id": OWNER.user_id} if params == (5,) else None
        elif flat.startswith("SELECT id, gym_id FROM gym_equipment"):
            self._result = {"id": params[0], "gym_id": 5} if params == (77,) else None
        elif flat.startswith("SELECT gym_id FROM gym_equipment"):
            self._result = {"gym_id": 5} if params == (77,) else None
        elif flat.startswith("SELECT id FROM equipment"):
            self._result = {"id": params[0]}
        elif flat.startswith("SELECT * FROM gym_equipment"):
            self._result = {"id": 77, "gym_id": 5, "mapping_id": 3, "equipment_id": 9, "is_active": 1}
        else:
            self._result = None

    def fetchone(self):
        return self._result

    def writes(self):
        return [sql for sql, _ in self.executed if sql.startswith(("INSERT", "UPDATE", "DELETE"))]


class _Conn:
    def __init__(self, cur):
        self.cur = cur

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self.cur


@pytest.fixture
def ctx(monkeypatch):
    cur = _Cursor()
    monkeypatch.setattr(gyms, "_connect", lambda: _Conn(cur))
    monkeypatch.setattr(security, "resolve_identity", lambda token: IDENTITIES[token])
    app = FastAPI()
    app.include_router(gyms.router)
    return TestClient(app, raise_server_exceptions=False), cur


def _h(who):
    return {"X-API-Token": who}


POST_BODY = {"gym_id": 5, "mapping_id": 3, "equipment_id": 9}


@pytest.mark.parametrize("who,status", [("other", 403), ("owner", 200), ("admin", 200)])
def test_post_gym_equipment_matriz(ctx, who, status):
    client, cur = ctx
    resp = client.post("/gym-equipment", json=POST_BODY, headers=_h(who))
    assert resp.status_code == status
    assert bool(cur.writes()) == (status == 200)
    if status == 200:
        assert resp.json()["item"]["id"] == 77


def test_post_gym_equipment_academia_inexistente_da_404(ctx):
    client, cur = ctx
    resp = client.post("/gym-equipment", json={**POST_BODY, "gym_id": 999}, headers=_h("admin"))
    assert resp.status_code == 404
    assert cur.writes() == []


@pytest.mark.parametrize("who,status", [("other", 403), ("owner", 200), ("admin", 200)])
def test_put_gym_equipment_matriz(ctx, who, status):
    client, cur = ctx
    resp = client.put("/gym-equipment/77", json={"is_active": 0}, headers=_h(who))
    assert resp.status_code == status
    assert bool(cur.writes()) == (status == 200)


def test_put_gym_equipment_vinculo_inexistente_da_404(ctx):
    client, cur = ctx
    resp = client.put("/gym-equipment/12345", json={"is_active": 0}, headers=_h("admin"))
    assert resp.status_code == 404
    assert cur.writes() == []


@pytest.mark.parametrize("who,status", [("other", 403), ("owner", 200), ("admin", 200)])
def test_delete_gym_equipment_matriz(ctx, who, status):
    client, cur = ctx
    resp = client.delete("/gym-equipment/77", headers=_h(who))
    assert resp.status_code == status
    assert bool(cur.writes()) == (status == 200)
    if status == 200:
        assert resp.json() == {"status": "ok", "id": 77, "is_active": False}


def test_delete_gym_equipment_vinculo_inexistente_da_404(ctx):
    client, cur = ctx
    resp = client.delete("/gym-equipment/12345", headers=_h("admin"))
    assert resp.status_code == 404
    assert cur.writes() == []


# ---------------------------------------------------------------------------
# muscle-group-body-map
# ---------------------------------------------------------------------------


@pytest.fixture
def body_map_client(monkeypatch):
    monkeypatch.setattr(services, "list_muscle_group_body_map", lambda: [{"muscle_group": "peito", "slugs": ["chest"]}])
    saved = []
    monkeypatch.setattr(
        services,
        "upsert_muscle_group_body_map",
        lambda group, slugs: saved.append((group, slugs)) or {"status": "ok", "muscle_group": group, "slugs": slugs},
    )
    app = FastAPI()
    app.include_router(dashboard_modules.router)
    state = {"identity": ADMIN}
    app.dependency_overrides[get_current_identity] = lambda: state["identity"]
    return TestClient(app, raise_server_exceptions=False), state, saved


def test_put_body_map_regular_403_e_nao_grava(body_map_client):
    client, state, saved = body_map_client
    state["identity"] = OTHER
    resp = client.put("/analytics/muscle-group-body-map", json={"muscle_group": "peito", "slugs": ["chest"]})
    assert resp.status_code == 403
    assert saved == []


def test_put_body_map_admin_200(body_map_client):
    client, state, saved = body_map_client
    resp = client.put("/analytics/muscle-group-body-map", json={"muscle_group": "peito", "slugs": ["chest"]})
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "muscle_group": "peito", "slugs": ["chest"]}
    assert saved == [("peito", ["chest"])]


@pytest.mark.parametrize("who", [OTHER, ADMIN])
def test_get_body_map_continua_200_para_qualquer_login(body_map_client, who):
    client, state, _ = body_map_client
    state["identity"] = who
    resp = client.get("/analytics/muscle-group-body-map")
    assert resp.status_code == 200
    assert resp.json()["items"][0]["muscle_group"] == "peito"
