"""M40 (unit, sem banco): dispositivos por usuario.

1. `security._device_identity` / `resolve_identity`: o token de dispositivo
   resolve para o DONO (`devices.user_id`) com o papel dele - nunca mais o
   admin -, rejeita revogado/inativo/dono inativo e so regrava
   `last_seen_at` quando o SELECT diz que passou do throttle.
2. Rotas `/devices*`: criacao para o usuario da credencial, token `mfd_`
   devolvido uma vez e guardado so como SHA-256, dispositivo nao gerencia
   dispositivo, isolamento entre usuarios e `GET /devices/me`.

O banco e um duble roteirizado por SQL; a prova contra MySQL real (os dois
backends) esta em
`tests/integration/dataapi/test_integracoes_metricas_sem_ddl_paridade.py`.
"""

from __future__ import annotations

import hashlib
from contextlib import contextmanager

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import devices, security
from app.security import Identity, get_current_identity

ADMIN = 1
ALUNO = 7
OUTRO = 9
TOKEN_RELOGIO = "mfd_token-cru-do-relogio-do-aluno"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class FakeDevicesDB:
    """Tabelas `users` e `devices` em memoria, com o SQL que o modulo emite."""

    def __init__(self) -> None:
        self.users = {
            ADMIN: {"id": ADMIN, "username": "doug", "full_name": "Doug", "role": "admin", "is_active": 1},
            ALUNO: {"id": ALUNO, "username": "aluno", "full_name": "Aluno Teste", "role": "regular", "is_active": 1},
            OUTRO: {"id": OUTRO, "username": "outro", "full_name": None, "role": "personal_trainer", "is_active": 1},
        }
        self.devices: dict[int, dict] = {}
        self.updates_last_seen: list[int] = []
        self.statements: list[str] = []

    def add_device(self, user_id: int, token: str, **extra) -> int:
        device_id = len(self.devices) + 1
        self.devices[device_id] = {
            "id": device_id,
            "user_id": user_id,
            "device_name": extra.get("device_name", f"Relogio {device_id}"),
            "platform": "zepp",
            "token_hash": _hash(token),
            "paired_at": f"2026-10-0{device_id} 10:00:00",
            "last_seen_at": extra.get("last_seen_at"),
            "revoked_at": extra.get("revoked_at"),
            "is_active": extra.get("is_active", 1),
            # O duble nao tem relogio: o teste diz se o throttle ja passou.
            "_throttle_vencido": extra.get("throttle_vencido", True),
        }
        return device_id

    @contextmanager
    def transaction(self):
        yield _Conn(self)


class _Conn:
    def __init__(self, db: FakeDevicesDB) -> None:
        self.db = db

    def cursor(self):
        return _Cursor(self.db)


class _Cursor:
    def __init__(self, db: FakeDevicesDB) -> None:
        self.db = db
        self._rows: list[dict] = []
        self.rowcount = 0
        self.lastrowid = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql: str, params=None):
        sql_norm = " ".join(sql.split())
        self.db.statements.append(sql_norm)
        db = self.db
        if "FROM devices d JOIN users u" in sql_norm and "token_hash = %s" in sql_norm:
            assert f"INTERVAL {security.DEVICE_LAST_SEEN_THROTTLE_MINUTES} MINUTE" in sql_norm
            (token_hash,) = params
            self._rows = [
                {
                    "device_id": d["id"],
                    "user_id": d["user_id"],
                    "role": db.users[d["user_id"]]["role"],
                    "is_active": db.users[d["user_id"]]["is_active"],
                    "needs_touch": 1 if (d["last_seen_at"] is None or d["_throttle_vencido"]) else 0,
                }
                for d in db.devices.values()
                if d["token_hash"] == token_hash and d["is_active"] == 1 and d["revoked_at"] is None
            ]
        elif "FROM user_sessions" in sql_norm:
            # Token de dispositivo nunca e sessao: `resolve_identity` cai aqui e nao acha.
            self._rows = []
        elif sql_norm.startswith("UPDATE devices SET last_seen_at"):
            (device_id,) = params
            db.updates_last_seen.append(device_id)
            db.devices[device_id]["last_seen_at"] = "agora"
            self.rowcount = 1
        elif sql_norm.startswith("INSERT INTO devices"):
            user_id, name, platform, token_hash = params
            device_id = len(db.devices) + 1
            db.devices[device_id] = {
                "id": device_id, "user_id": user_id, "device_name": name, "platform": platform,
                "token_hash": token_hash, "paired_at": "2026-10-18 10:00:00", "last_seen_at": None,
                "revoked_at": None, "is_active": 1, "_throttle_vencido": True,
            }
            self.lastrowid = device_id
        elif sql_norm.startswith("SELECT id, device_name") and "FROM devices" in sql_norm:
            (user_id,) = params
            so_ativos = "is_active = 1" in sql_norm
            self._rows = [
                {k: d[k] for k in ("id", "device_name", "platform", "paired_at", "last_seen_at", "revoked_at", "is_active")}
                for d in sorted(db.devices.values(), key=lambda d: d["paired_at"], reverse=True)
                if d["user_id"] == user_id and (not so_ativos or (d["is_active"] == 1 and d["revoked_at"] is None))
            ]
        elif "FROM devices d JOIN users u" in sql_norm and "d.id = %s" in sql_norm:
            device_id, user_id = params
            d = db.devices.get(device_id)
            self._rows = []
            if d and d["user_id"] == user_id:
                u = db.users[user_id]
                self._rows = [{
                    "device_id": d["id"], "device_name": d["device_name"], "platform": d["platform"],
                    "last_seen_at": d["last_seen_at"], "full_name": u["full_name"], "username": u["username"],
                }]
        elif sql_norm.startswith("UPDATE devices SET is_active = 0"):
            device_id, user_id = params
            d = db.devices.get(device_id)
            if d and d["user_id"] == user_id and d["is_active"] == 1:
                d["is_active"] = 0
                d["revoked_at"] = "agora"
                self.rowcount = 1
            else:
                self.rowcount = 0
        else:  # pragma: no cover - SQL novo precisa de roteiro explicito
            raise AssertionError(f"SQL nao roteirizado: {sql_norm}")
        return self

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


@pytest.fixture
def db(monkeypatch) -> FakeDevicesDB:
    fake = FakeDevicesDB()
    monkeypatch.setattr(security, "transaction", fake.transaction)
    monkeypatch.setattr(devices, "transaction", fake.transaction)
    monkeypatch.delenv("API_TOKEN", raising=False)
    # Se alguem voltar a resolver dispositivo para o admin, o teste explode.
    monkeypatch.setattr(security, "_admin_user_id", lambda: pytest.fail("dispositivo nao consulta o admin"))
    return fake


# ---------------------------------------------------------------------------
# 1. Identidade do dispositivo
# ---------------------------------------------------------------------------


def test_token_de_dispositivo_resolve_para_o_dono_e_nao_para_o_admin(db):
    device_id = db.add_device(ALUNO, TOKEN_RELOGIO)

    identity = security.resolve_identity(TOKEN_RELOGIO)

    assert identity == Identity(user_id=ALUNO, role="regular", source="device", device_id=device_id)


def test_papel_vem_do_dono(db):
    db.add_device(OUTRO, TOKEN_RELOGIO)
    assert security.resolve_identity(TOKEN_RELOGIO).role == "personal_trainer"


def test_dispositivo_do_admin_continua_admin(db):
    # Os dispositivos ja existentes em producao sao do admin: mesmo resultado de antes.
    db.add_device(ADMIN, TOKEN_RELOGIO)
    identity = security.resolve_identity(TOKEN_RELOGIO)
    assert (identity.user_id, identity.role, identity.source) == (ADMIN, "admin", "device")


@pytest.mark.parametrize(
    "extra",
    [{"revoked_at": "2026-10-01 10:00:00"}, {"is_active": 0}],
    ids=["revogado", "inativo"],
)
def test_dispositivo_revogado_ou_inativo_e_401(db, extra):
    db.add_device(ALUNO, TOKEN_RELOGIO, **extra)
    with pytest.raises(HTTPException) as exc:
        security.resolve_identity(TOKEN_RELOGIO)
    assert exc.value.status_code == 401
    assert security.device_token_matches(TOKEN_RELOGIO) is False


def test_dono_inativo_e_401(db):
    db.add_device(ALUNO, TOKEN_RELOGIO)
    db.users[ALUNO]["is_active"] = 0
    with pytest.raises(HTTPException) as exc:
        security.resolve_identity(TOKEN_RELOGIO)
    assert exc.value.status_code == 401


def test_last_seen_gravado_quando_nunca_houve_ou_throttle_vencido(db):
    device_id = db.add_device(ALUNO, TOKEN_RELOGIO)
    security.resolve_identity(TOKEN_RELOGIO)
    assert db.updates_last_seen == [device_id]


def test_last_seen_nao_regravado_dentro_do_throttle(db):
    db.add_device(ALUNO, TOKEN_RELOGIO, last_seen_at="2026-10-18 09:58:00", throttle_vencido=False)
    for _ in range(3):
        security.resolve_identity(TOKEN_RELOGIO)
    assert db.updates_last_seen == []
    assert not any(s.startswith("UPDATE") for s in db.statements)


def test_throttle_e_de_dez_minutos():
    assert security.DEVICE_LAST_SEEN_THROTTLE_MINUTES == 10


def test_require_token_aceita_dispositivo_ativo(db, monkeypatch):
    monkeypatch.setenv("API_TOKEN", "outro-token-mestre")
    db.add_device(ALUNO, TOKEN_RELOGIO)
    assert security.require_token(TOKEN_RELOGIO) is True


# ---------------------------------------------------------------------------
# 2. Rotas
# ---------------------------------------------------------------------------


def _client(identity: Identity | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(devices.router)
    if identity is not None:
        app.dependency_overrides[get_current_identity] = lambda: identity
    return TestClient(app, raise_server_exceptions=False)


SESSAO_ALUNO = Identity(user_id=ALUNO, role="regular", source="session")
SESSAO_OUTRO = Identity(user_id=OUTRO, role="regular", source="session")


def test_router_esta_no_app_real_sem_token_mestre():
    from app.main import app

    caminhos = app.openapi()["paths"]
    for caminho in ("/devices/pair", "/devices", "/devices/me", "/devices/{device_id}"):
        assert caminho in caminhos, caminho
    cliente = TestClient(app, raise_server_exceptions=False)
    # Sem credencial: 401 vindo de `get_current_identity` (nao depende de API_TOKEN).
    assert cliente.post("/devices/pair", json={"device_name": "x"}).status_code == 401


def test_parear_cria_para_o_usuario_da_sessao_e_devolve_token_uma_vez(db):
    resposta = _client(SESSAO_ALUNO).post("/devices/pair", json={"device_name": "  Amazfit Balance  "})

    assert resposta.status_code == 200
    corpo = resposta.json()
    token = corpo["device_token"]
    assert token.startswith("mfd_")
    assert len(token) > len("mfd_") + 40
    assert corpo["device_name"] == "Amazfit Balance"
    assert corpo["platform"] == "zepp"

    linha = db.devices[corpo["device_id"]]
    assert linha["user_id"] == ALUNO
    assert linha["token_hash"] == _hash(token)
    assert token not in str(db.devices)

    # O token devolvido autentica como o aluno.
    assert security.resolve_identity(token).user_id == ALUNO
    # Listagem nunca expoe token nem hash.
    itens = _client(SESSAO_ALUNO).get("/devices").json()["items"]
    assert "device_token" not in itens[0] and "token_hash" not in itens[0]


@pytest.mark.parametrize("nome", ["", "   ", "x" * (devices.DEVICE_NAME_MAX_LENGTH + 1)])
def test_parear_valida_nome(db, nome):
    resposta = _client(SESSAO_ALUNO).post("/devices/pair", json={"device_name": nome})
    assert resposta.status_code == 422
    assert db.devices == {}


def test_tokens_sao_distintos(db):
    cliente = _client(SESSAO_ALUNO)
    a = cliente.post("/devices/pair", json={"device_name": "A"}).json()["device_token"]
    b = cliente.post("/devices/pair", json={"device_name": "B"}).json()["device_token"]
    assert a != b


def test_dispositivo_nao_cria_lista_nem_revoga_dispositivo(db):
    device_id = db.add_device(ALUNO, TOKEN_RELOGIO)
    cliente = _client(Identity(user_id=ALUNO, role="regular", source="device", device_id=device_id))

    assert cliente.post("/devices/pair", json={"device_name": "clone"}).status_code == 403
    assert cliente.get("/devices").status_code == 403
    assert cliente.delete(f"/devices/{device_id}").status_code == 403
    assert len(db.devices) == 1
    assert db.devices[device_id]["is_active"] == 1


def test_dispositivo_nao_cria_outro_pelo_token_real(db):
    # Sem override: o caminho completo `X-API-Token` -> resolve_identity -> 403.
    db.add_device(ALUNO, TOKEN_RELOGIO)
    resposta = _client().post("/devices/pair", json={"device_name": "clone"}, headers={"X-API-Token": TOKEN_RELOGIO})
    assert resposta.status_code == 403
    assert len(db.devices) == 1


def test_lista_so_os_do_dono_e_status(db):
    meu_ativo = db.add_device(ALUNO, "t1")
    meu_revogado = db.add_device(ALUNO, "t2", is_active=0, revoked_at="2026-10-02 10:00:00")
    db.add_device(OUTRO, "t3")

    cliente = _client(SESSAO_ALUNO)
    ativos = cliente.get("/devices").json()["items"]
    todos = cliente.get("/devices", params={"include_revoked": "true"}).json()["items"]

    assert [i["id"] for i in ativos] == [meu_ativo]
    assert {i["id"]: i["status"] for i in todos} == {meu_ativo: "active", meu_revogado: "revoked"}


def test_revogar_dispositivo_de_outro_usuario_e_404(db):
    do_outro = db.add_device(OUTRO, "t3")
    resposta = _client(SESSAO_ALUNO).delete(f"/devices/{do_outro}")
    assert resposta.status_code == 404
    assert db.devices[do_outro]["is_active"] == 1


def test_revogar_o_proprio_e_o_token_deixa_de_valer(db):
    device_id = db.add_device(ALUNO, TOKEN_RELOGIO)
    cliente = _client(SESSAO_ALUNO)

    assert cliente.delete(f"/devices/{device_id}").json() == {"status": "ok", "device_id": device_id, "revoked": True}
    assert cliente.delete(f"/devices/{device_id}").status_code == 404
    with pytest.raises(HTTPException) as exc:
        security.resolve_identity(TOKEN_RELOGIO)
    assert exc.value.status_code == 401


def test_devices_me_com_token_de_dispositivo(db):
    device_id = db.add_device(ALUNO, TOKEN_RELOGIO, device_name="Balance do aluno")

    resposta = _client().get("/devices/me", headers={"X-API-Token": TOKEN_RELOGIO})

    assert resposta.status_code == 200
    assert resposta.json() == {
        "device_id": device_id,
        "device_name": "Balance do aluno",
        "platform": "zepp",
        "user_display_name": "Aluno Teste",
        "last_seen_at": "agora",
    }


def test_devices_me_sem_nome_completo_usa_username(db):
    db.add_device(OUTRO, TOKEN_RELOGIO)
    resposta = _client().get("/devices/me", headers={"X-API-Token": TOKEN_RELOGIO})
    assert resposta.json()["user_display_name"] == "outro"


def test_devices_me_exige_token_de_dispositivo(db):
    assert _client(SESSAO_ALUNO).get("/devices/me").status_code == 403


def test_devices_me_com_token_revogado_e_401(db):
    db.add_device(ALUNO, TOKEN_RELOGIO, is_active=0, revoked_at="2026-10-02 10:00:00")
    assert _client().get("/devices/me", headers={"X-API-Token": TOKEN_RELOGIO}).status_code == 401
