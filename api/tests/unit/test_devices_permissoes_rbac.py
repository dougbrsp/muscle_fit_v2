"""RBAC das rotas /devices* (ADR 0011, lote 2B): permissao `conta.dispositivos`.

O resolvedor REAL de permissoes (`access.service.resolve_permissions`) roda sobre um
banco falso de concessoes que conta as consultas e devolve linhas no formato real
(`group_key, is_system, resource, action, scope`). O banco de dispositivos e um duble
minimo que avalia o proprio SQL (so as quatro instrucoes que o modulo emite).

Provas: credencial de dispositivo continua recusada em pair/list/revoke com o 403 de
antes e SEM consultar permissoes; sessao sem a concessao = 403 `forbidden`; com a
concessao passa; `GET /devices/me` depende da concessao do DONO do dispositivo; falha do
banco na resolucao = 503; token mestre passa sem consultar permissoes.

Observacao: o fake de permissoes e proprio deste arquivo (os arquivos de teste unitarios
nao sao importaveis entre si, nao ha pacote `tests`).

Rodar: cd api && python -m pytest tests/unit/test_devices_permissoes_rbac.py
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import devices
from app.access import service as access_service
from app.security import Identity, get_current_identity

DONO_COM = 7
DONO_SEM = 8
CONCEDER = [("conta.dispositivos", a, "proprios") for a in ("ver", "editar", "excluir")]


class FakePermissoes:
    def __init__(self, concessoes_por_usuario, falha=None):
        self.por_usuario = concessoes_por_usuario
        self.falha = falha
        self.consultas = []

    def fetch_rows(self, sql, params=()):
        self.consultas.append(tuple(params))
        if self.falha is not None:
            raise self.falha
        (user_id,) = params
        if user_id not in self.por_usuario:
            return []  # usuario sem grupo
        grants = self.por_usuario[user_id]
        if not grants:
            return [{"group_key": "g", "is_system": 0, "resource": None, "action": None, "scope": None}]
        return [
            {"group_key": "g", "is_system": 0, "resource": r, "action": a, "scope": s}
            for r, a, s in grants
        ]


class FakeDevicesDb:
    def __init__(self):
        self.log = []

    @contextmanager
    def transaction(self):
        yield SimpleNamespace(cursor=lambda: _Cur(self))


class _Cur:
    def __init__(self, db):
        self.db = db
        self.rowcount = 1
        self.lastrowid = 55
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        flat = " ".join(sql.split())
        self.db.log.append(flat)
        if flat.startswith("SELECT d.id AS device_id"):
            self._rows = [{
                "device_id": params[0], "device_name": "Relogio", "platform": "zepp",
                "last_seen_at": None, "full_name": "Dono", "username": "dono",
            }]
        elif flat.startswith("SELECT id, device_name"):
            self._rows = []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


@pytest.fixture
def cenario(monkeypatch):
    db = FakeDevicesDb()
    perms = FakePermissoes({DONO_COM: CONCEDER, DONO_SEM: []})
    monkeypatch.setattr(devices, "transaction", db.transaction)
    monkeypatch.setattr(access_service, "_db", perms)
    return db, perms


def _client(identity):
    app = FastAPI()
    app.include_router(devices.router)
    app.dependency_overrides[get_current_identity] = lambda: identity
    return TestClient(app, raise_server_exceptions=False)


def _sessao(uid):
    return Identity(user_id=uid, role="regular", source="session")


def _dispositivo(uid):
    return Identity(user_id=uid, role="regular", source="device", device_id=3)


MESTRE = Identity(user_id=1, role="admin", source="master_token")

ROTAS_USUARIO = [
    ("post", "/devices/pair", {"device_name": "Relogio"}),
    ("get", "/devices", None),
    ("delete", "/devices/3", None),
]


def _chamar(client, metodo, caminho, corpo):
    if corpo is not None:
        return getattr(client, metodo)(caminho, json=corpo)
    return getattr(client, metodo)(caminho)


@pytest.mark.parametrize("metodo,caminho,corpo", ROTAS_USUARIO)
def test_credencial_de_dispositivo_continua_403_sem_consultar_permissoes(cenario, metodo, caminho, corpo):
    db, perms = cenario
    resp = _chamar(_client(_dispositivo(DONO_COM)), metodo, caminho, corpo)
    assert resp.status_code == 403
    assert resp.json()["detail"] == devices.DEVICE_MANAGEMENT_FORBIDDEN_DETAIL
    assert perms.consultas == []
    assert db.log == []


@pytest.mark.parametrize("metodo,caminho,corpo", ROTAS_USUARIO)
def test_sessao_sem_a_concessao_recebe_403_forbidden(cenario, metodo, caminho, corpo):
    db, _ = cenario
    resp = _chamar(_client(_sessao(DONO_SEM)), metodo, caminho, corpo)
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"
    assert db.log == []  # nada chegou ao banco de dispositivos


@pytest.mark.parametrize("metodo,caminho,corpo", ROTAS_USUARIO)
def test_sessao_sem_grupo_recebe_403(cenario, metodo, caminho, corpo):
    resp = _chamar(_client(_sessao(999)), metodo, caminho, corpo)
    assert resp.status_code == 403


@pytest.mark.parametrize("metodo,caminho,corpo", ROTAS_USUARIO)
def test_sessao_com_a_concessao_passa(cenario, metodo, caminho, corpo):
    _, perms = cenario
    resp = _chamar(_client(_sessao(DONO_COM)), metodo, caminho, corpo)
    assert resp.status_code == 200, resp.text
    assert perms.consultas, "a concessao deve vir do resolvedor real"


def test_pair_grava_para_o_dono_da_sessao(cenario):
    db, _ = cenario
    resp = _client(_sessao(DONO_COM)).post("/devices/pair", json={"device_name": "Relogio"})
    assert resp.status_code == 200
    assert any(s.startswith("INSERT INTO devices") for s in db.log)


def test_devices_me_com_dono_que_tem_a_concessao_passa(cenario):
    resp = _client(_dispositivo(DONO_COM)).get("/devices/me")
    assert resp.status_code == 200, resp.text
    assert resp.json()["device_id"] == 3


def test_devices_me_com_dono_sem_a_concessao_e_403(cenario):
    db, perms = cenario
    resp = _client(_dispositivo(DONO_SEM)).get("/devices/me")
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"
    assert perms.consultas, "dispositivo consulta as permissoes do dono"
    assert db.log == []


def test_devices_me_com_sessao_continua_403_de_so_dispositivo(cenario):
    resp = _client(_sessao(DONO_COM)).get("/devices/me")
    assert resp.status_code == 403
    assert resp.json()["detail"] == devices.DEVICE_ONLY_DETAIL


@pytest.mark.parametrize(
    "identidade_fn,metodo,caminho,corpo",
    [(_sessao, m, c, b) for m, c, b in ROTAS_USUARIO] + [(_dispositivo, "get", "/devices/me", None)],
)
def test_falha_do_banco_na_resolucao_e_503_nunca_200_nem_403(cenario, monkeypatch, identidade_fn, metodo, caminho, corpo):
    db, _ = cenario
    monkeypatch.setattr(access_service, "_db", FakePermissoes({DONO_COM: CONCEDER}, falha=RuntimeError("banco fora")))
    resp = _chamar(_client(identidade_fn(DONO_COM)), metodo, caminho, corpo)
    assert resp.status_code == 503, resp.text
    assert db.log == []


@pytest.mark.parametrize("metodo,caminho,corpo", ROTAS_USUARIO)
def test_token_mestre_passa_sem_consultar_permissoes(cenario, metodo, caminho, corpo):
    _, perms = cenario
    resp = _chamar(_client(MESTRE), metodo, caminho, corpo)
    assert resp.status_code == 200, resp.text
    assert perms.consultas == []
