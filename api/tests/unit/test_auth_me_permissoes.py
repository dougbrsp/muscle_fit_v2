"""`GET /auth/me` expoe `permissions` e `groups` (RBAC, ADR 0011, lote 1), sem
mudar nenhum campo anterior; se a resolucao falhar por indisponibilidade do banco
(`AuthBackendUnavailable`) responde 200 com `permissions`/`groups` nulos
(desconhecido, diferente de `{}`/`[]` = nenhuma permissao) e log de aviso; continua
isenta da trava de troca de senha obrigatoria (SEG-06).

Rodar: cd api && python -m pytest tests/unit/test_auth_me_permissoes.py
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import auth, security
from app.access import service
from app.access.catalog import RESOURCE_KEYS
from app.security import Identity, get_current_identity

USER_ROW = {
    "id": 10,
    "username": "aluno",
    "full_name": "Aluno Teste",
    "role": "regular",
    "must_change_password": 0,
    "intervals_athlete_id": None,
    "intervals_api_key_encrypted": None,
    "timezone": "America/Sao_Paulo",
    "sex": "M",
    "birth_date": date(1990, 5, 1),
    "height_cm": Decimal("178.5"),
    "activity_level": "moderate",
}


class _Cursor:
    def __init__(self, row):
        self.row = row

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        assert sql.startswith("SELECT * FROM users")

    def fetchone(self):
        return self.row


class _Conn:
    def __init__(self, row):
        self.row = row

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return _Cursor(self.row)


class _Banco:
    """grupos/concessoes por user_id; `error` simula falha da consulta."""

    def __init__(self, rows=None, error=None):
        self.rows = rows or {}
        self.error = error
        self.calls = 0

    def __call__(self, sql, params=()):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return [
            {
                "group_key": g,
                "is_system": 1 if g in ("admin", "personal_trainer", "usuario") else 0,
                "resource": r,
                "action": a,
                "scope": s,
            }
            for g, r, a, s in self.rows.get(params[0], [])
        ]


@pytest.fixture
def cliente(monkeypatch):
    app = FastAPI()
    app.include_router(auth.router)
    identity = Identity(user_id=10, role="regular", source="session")
    app.dependency_overrides[get_current_identity] = lambda: identity
    monkeypatch.setattr(auth, "transaction", lambda: _Conn(dict(USER_ROW)))
    monkeypatch.setattr(auth, "exercise_cards_enabled", lambda: False)

    def _instalar(banco: _Banco) -> TestClient:
        monkeypatch.setattr(service._db, "fetch_rows", banco)
        return TestClient(app, raise_server_exceptions=False)

    return _instalar


def test_me_devolve_permissions_e_groups(cliente):
    banco = _Banco(
        {
            10: [
                ("usuario", "cardio", "ver", "proprios"),
                ("usuario", "cardio", "editar", "proprios"),
                ("personal_trainer", "cardio", "ver", "todos"),
            ]
        }
    )
    resp = cliente(banco).get("/auth/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["permissions"] == {"cardio": {"ver": "todos", "editar": "proprios"}}
    assert body["groups"] == ["usuario", "personal_trainer"]


def test_me_usuario_sem_grupo_recebe_objeto_e_lista_vazios(cliente):
    resp = cliente(_Banco()).get("/auth/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["permissions"] == {} and body["groups"] == []
    assert body["role"] == "regular", "sem grupo nao ha fallback: role segue como informacao"


def test_me_preserva_todos_os_campos_anteriores_com_os_mesmos_valores(cliente):
    esperado = auth._public_user(dict(USER_ROW))
    esperado["source"] = "session"
    esperado["features"] = {"exercise_cards": False}
    body = cliente(_Banco({10: [("usuario", "cardio", "ver", "proprios")]})).get("/auth/me").json()
    assert {k: v for k, v in body.items() if k not in ("permissions", "groups")} == esperado
    assert set(body) == set(esperado) | {"permissions", "groups"}
    assert "password_hash" not in body and "intervals_api_key_encrypted" not in body


def test_me_grupo_admin_lista_todos_os_recursos_do_catalogo(cliente):
    banco = _Banco({10: [("admin", None, None, None)]})
    body = cliente(banco).get("/auth/me").json()
    assert body["groups"] == ["admin"]
    assert set(body["permissions"]) == set(RESOURCE_KEYS)
    assert all(acoes == {"ver": "todos", "editar": "todos", "excluir": "todos"} for acoes in body["permissions"].values())


def test_me_ignora_concessao_desconhecida_do_banco(cliente):
    banco = _Banco({10: [("usuario", "recurso.removido", "ver", "todos"), ("usuario", "cardio", "ver", "proprios")]})
    body = cliente(banco).get("/auth/me").json()
    assert body["permissions"] == {"cardio": {"ver": "proprios"}}


def _esperado_campos_atuais() -> dict:
    esperado = auth._public_user(dict(USER_ROW))
    esperado["source"] = "session"
    esperado["features"] = {"exercise_cards": False}
    return esperado


def test_me_falha_de_banco_na_resolucao_degrada_para_200_com_permissions_e_groups_nulos(cliente, caplog):
    with caplog.at_level(logging.WARNING, logger="fitness.auth"):
        resp = cliente(_Banco(error=RuntimeError("Aurora fora do ar"))).get("/auth/me")
    assert resp.status_code == 200
    body = resp.json()
    assert "permissions" in body and body["permissions"] is None
    assert "groups" in body and body["groups"] is None
    assert {k: v for k, v in body.items() if k not in ("permissions", "groups")} == _esperado_campos_atuais()
    avisos = [r for r in caplog.records if getattr(r, "event", None) == "auth_me_permissions_degraded"]
    assert len(avisos) == 1 and avisos[0].levelno == logging.WARNING


def test_me_degradado_nao_vaza_a_mensagem_da_excecao_do_banco(cliente):
    resp = cliente(_Banco(error=RuntimeError("segredo-interno-do-driver"))).get("/auth/me")
    assert "segredo-interno-do-driver" not in resp.text


def test_me_sem_falha_nao_loga_o_aviso_de_degradacao(cliente, caplog):
    with caplog.at_level(logging.WARNING, logger="fitness.auth"):
        cliente(_Banco({10: [("usuario", "cardio", "ver", "proprios")]})).get("/auth/me")
    assert not [r for r in caplog.records if getattr(r, "event", None) == "auth_me_permissions_degraded"]


def test_me_sem_grupo_continua_objeto_e_lista_vazios_distinguiveis_de_nulo(cliente):
    sem_grupo = cliente(_Banco()).get("/auth/me").json()
    assert sem_grupo["permissions"] == {} and sem_grupo["permissions"] is not None
    assert sem_grupo["groups"] == [] and sem_grupo["groups"] is not None
    degradado = cliente(_Banco(error=RuntimeError("x"))).get("/auth/me").json()
    assert (degradado["permissions"], degradado["groups"]) == (None, None)
    assert (sem_grupo["permissions"], sem_grupo["groups"]) != (degradado["permissions"], degradado["groups"])


def test_me_valueerror_na_resolucao_nao_e_engolido(cliente, monkeypatch, caplog):
    def _quebra(identity):
        raise ValueError("bug de programacao")

    http = cliente(_Banco())
    monkeypatch.setattr(auth, "resolve_permissions", _quebra)
    with caplog.at_level(logging.WARNING, logger="fitness.auth"):
        resp = http.get("/auth/me")
    assert resp.status_code == 500
    assert "permissions" not in resp.text
    assert not [r for r in caplog.records if getattr(r, "event", None) == "auth_me_permissions_degraded"]


@pytest.mark.parametrize("codigo", [401, 403])
def test_me_httpexception_comum_na_resolucao_nao_e_engolida(cliente, monkeypatch, codigo):
    def _nega(identity):
        raise HTTPException(status_code=codigo, detail="negado")

    http = cliente(_Banco())
    monkeypatch.setattr(auth, "resolve_permissions", _nega)
    resp = http.get("/auth/me")
    assert resp.status_code == codigo
    assert "permissions" not in resp.json()


def test_me_httpexception_503_que_nao_e_authbackendunavailable_nao_e_engolida(cliente, monkeypatch):
    def _outra(identity):
        raise HTTPException(status_code=503, detail="outro 503")

    http = cliente(_Banco())
    monkeypatch.setattr(auth, "resolve_permissions", _outra)
    assert http.get("/auth/me").status_code == 503


def test_servico_chamado_direto_continua_levantando_503_e_nao_degrada(cliente, monkeypatch):
    monkeypatch.setattr(service._db, "fetch_rows", _Banco(error=RuntimeError("Aurora fora do ar")))
    ident = Identity(user_id=10, role="regular", source="session")
    with pytest.raises(security.AuthBackendUnavailable) as exc:
        service.resolve_permissions(ident)
    assert exc.value.status_code == 503 and exc.value.headers.get("Retry-After")
    with pytest.raises(security.AuthBackendUnavailable):
        service.authorize_target(ident, "cardio", "ver")


def test_dependencia_require_permission_continua_503_em_falha_de_banco(cliente, monkeypatch):
    from app.access import require_permission

    app = FastAPI()

    @app.get("/protegida")
    def protegida(_: Identity = Depends(require_permission("cardio", "ver"))):
        return {"ok": True}

    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=10, role="regular", source="session")
    monkeypatch.setattr(service._db, "fetch_rows", _Banco(error=RuntimeError("Aurora fora do ar")))
    resp = TestClient(app, raise_server_exceptions=False).get("/protegida")
    assert resp.status_code == 503 and resp.headers.get("retry-after")


def test_me_token_mestre_nao_consulta_grupos(monkeypatch):
    app = FastAPI()
    app.include_router(auth.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=1, role="admin", source="master_token")
    monkeypatch.setattr(auth, "transaction", lambda: _Conn({**USER_ROW, "id": 1, "role": "admin"}))
    monkeypatch.setattr(auth, "exercise_cards_enabled", lambda: False)
    banco = _Banco(error=AssertionError("nao consulta"))
    monkeypatch.setattr(service._db, "fetch_rows", banco)
    body = TestClient(app).get("/auth/me").json()
    assert set(body["permissions"]) == set(RESOURCE_KEYS) and banco.calls == 0


def test_me_usuario_inexistente_continua_404_sem_consultar_grupos(monkeypatch):
    app = FastAPI()
    app.include_router(auth.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=10, role="regular", source="session")
    monkeypatch.setattr(auth, "transaction", lambda: _Conn(None))
    banco = _Banco()
    monkeypatch.setattr(service._db, "fetch_rows", banco)
    assert TestClient(app).get("/auth/me").status_code == 404
    assert banco.calls == 0


# ---------------------------------------------------------------------------
# SEG-06: /auth/me segue isenta da troca de senha obrigatoria
# ---------------------------------------------------------------------------


def test_me_continua_na_lista_de_isencao_da_troca_de_senha():
    assert ("GET", "/auth/me") in security._MUST_CHANGE_EXEMPT


def test_me_responde_200_com_troca_de_senha_pendente_e_outras_rotas_levam_403(monkeypatch):
    app = FastAPI()
    app.include_router(auth.router)

    @app.get("/qualquer-outra")
    def outra(identity: Identity = Depends(get_current_identity)):
        return {"ok": True}

    pendente = Identity(user_id=10, role="regular", source="session", must_change_password=True)
    monkeypatch.setattr(security, "resolve_identity", lambda token: pendente)
    monkeypatch.setattr(auth, "transaction", lambda: _Conn({**USER_ROW, "must_change_password": 1}))
    monkeypatch.setattr(auth, "exercise_cards_enabled", lambda: False)
    monkeypatch.setattr(service._db, "fetch_rows", _Banco({10: [("usuario", "cardio", "ver", "proprios")]}))
    http = TestClient(app, raise_server_exceptions=False)
    headers = {"X-API-Token": "qualquer"}

    resp = http.get("/auth/me", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["permissions"] == {"cardio": {"ver": "proprios"}}
    bloqueada = http.get("/qualquer-outra", headers=headers)
    assert bloqueada.status_code == 403
    assert security.MUST_CHANGE_PASSWORD_DETAIL in str(bloqueada.json())
