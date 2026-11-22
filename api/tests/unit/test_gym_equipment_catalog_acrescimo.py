"""POST /gym-equipment-catalog (#163b): corpo validado, equipamento inativo recusado,
autorizacao antes de olhar o equipamento, idempotencia e `links` na resposta.

Rodar: cd api && python -m pytest tests/unit/test_gym_equipment_catalog_acrescimo.py
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import gyms, security
from app.access import service as access_service
from tests.unit.test_gyms_permissoes_rbac import IDENTIDADES, FakePermissoes, _chamar

EQUIPAMENTOS = {9: 1, 10: 0}  # id -> is_active (404 para qualquer outro)


class _Cur:
    def __init__(self, catalogo_ativo=None):
        self.executed = []
        self.catalogo = {} if catalogo_ativo is None else {9: catalogo_ativo}  # equipment_id -> is_active
        self.synced = False
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.executed.append((flat, params))
        self._rows = []
        if flat.startswith("SELECT owner_user_id FROM gyms"):
            owner = {5: 10, 6: None}.get(params[0], "x")
            self._rows = [] if owner == "x" else [{"owner_user_id": owner}]
        elif flat.startswith("SELECT id, is_active FROM equipment"):
            if params[0] in EQUIPAMENTOS:
                self._rows = [{"id": params[0], "is_active": EQUIPAMENTOS[params[0]]}]
        elif flat.startswith("INSERT INTO gym_equipment_catalog"):
            self.catalogo[params[1]] = 1  # novo ou reativado; nunca duplica a chave
        elif flat.startswith("SELECT * FROM gym_equipment_catalog"):
            self._rows = [{"id": 88, "gym_id": params[0], "equipment_id": params[1], "is_active": self.catalogo[params[1]]}]
        elif flat.startswith("SELECT id, mapping_id, equipment_id FROM gym_equipment"):
            assert self.synced, "links devem ser lidos depois do sync"
            self._rows = [{"id": 77, "mapping_id": 3, "equipment_id": params[1]}]

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def writes(self):
        return [s for s, _ in self.executed if s.startswith(("INSERT", "UPDATE", "DELETE"))]


class _Conn:
    def __init__(self, cur):
        self.cur = cur

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self.cur


def _montar(monkeypatch, cur):
    def _sync(conn, gym_id):
        cur.synced = True

    monkeypatch.setattr(gyms, "_connect", lambda: _Conn(cur))
    monkeypatch.setattr(gyms, "sync_gym_equipment_for_gym", _sync)
    monkeypatch.setattr(security, "resolve_identity", lambda token: IDENTIDADES[token])
    monkeypatch.setattr(access_service, "_db", FakePermissoes())
    app = FastAPI()
    app.include_router(gyms.router)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def ctx(monkeypatch):
    cur = _Cur()
    return _montar(monkeypatch, cur), cur


def _post(client, quem, corpo):
    return _chamar(client, quem, "POST", "/gym-equipment-catalog", corpo)


@pytest.mark.parametrize("equip", [9, 10, 999])
def test_nao_dono_recebe_403_igual_e_nada_e_gravado(ctx, equip):
    client, cur = ctx
    resp = _post(client, "terceiro", {"gym_id": 5, "equipment_id": equip})
    assert resp.status_code == 403, resp.text
    assert cur.writes() == []
    assert not any("FROM equipment" in s for s, _ in cur.executed)


def test_academia_inexistente_e_alheia_dao_a_mesma_resposta_sem_escopo_todos(ctx):
    client, cur = ctx
    alheia = _post(client, "terceiro", {"gym_id": 5, "equipment_id": 9})
    inexistente = _post(client, "terceiro", {"gym_id": 999, "equipment_id": 9})
    assert (alheia.status_code, alheia.json()) == (inexistente.status_code, inexistente.json())
    assert inexistente.status_code == 403
    assert cur.writes() == []
    assert not any("FROM equipment" in s for s, _ in cur.executed)


@pytest.mark.parametrize("quem,gym", [("dono", 5), ("admin", 5), ("admin", 6)])
def test_dono_e_admin_acrescentam_e_recebem_links(ctx, quem, gym):
    client, cur = ctx
    resp = _post(client, quem, {"gym_id": gym, "equipment_id": 9})
    assert resp.status_code == 200, resp.text
    corpo = resp.json()
    assert corpo["status"] == "ok" and corpo["item"]["equipment_id"] == 9
    assert corpo["links"] == [{"id": 77, "mapping_id": 3, "equipment_id": 9}]
    assert cur.synced


@pytest.mark.parametrize("equip,status", [(10, 409), (999, 404)])
def test_equipamento_inativo_ou_inexistente_e_recusado_sem_insert(ctx, equip, status):
    client, cur = ctx
    resp = _post(client, "admin", {"gym_id": 5, "equipment_id": equip})
    assert resp.status_code == status, resp.text
    assert cur.writes() == [] and not cur.synced


@pytest.mark.parametrize(
    "corpo",
    [
        {"gym_id": 5},
        {"equipment_id": 9},
        {},
        {"gym_id": "5", "equipment_id": 9},
        {"gym_id": 5, "equipment_id": "abc"},
        {"gym_id": 0, "equipment_id": 9},
        {"gym_id": 5, "equipment_id": 0},
        {"gym_id": -1, "equipment_id": 9},
        {"gym_id": 5, "equipment_id": -3},
        {"gym_id": True, "equipment_id": 9},
    ],
)
def test_corpo_invalido_da_422_sem_gravar(ctx, corpo):
    client, cur = ctx
    resp = _post(client, "admin", corpo)
    assert resp.status_code == 422, resp.text
    assert cur.executed == []


def test_repeticao_nao_duplica(ctx):
    client, cur = ctx
    for _ in range(2):
        assert _post(client, "dono", {"gym_id": 5, "equipment_id": 9}).status_code == 200
    assert cur.catalogo == {9: 1}
    assert all("ON DUPLICATE KEY UPDATE is_active = 1" in s for s in cur.writes())


def test_vinculo_inativo_e_reativado(monkeypatch):
    cur = _Cur(catalogo_ativo=0)
    client = _montar(monkeypatch, cur)
    resp = _post(client, "dono", {"gym_id": 5, "equipment_id": 9})
    assert resp.status_code == 200, resp.text
    assert resp.json()["item"]["is_active"] == 1
