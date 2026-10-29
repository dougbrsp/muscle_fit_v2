"""SEG-17: CORS explícito (origens, métodos e headers) nos apps FastAPI.

Preflight de origem permitida passa com credenciais; origem estranha não
recebe `Access-Control-Allow-Origin`; método/header fora da lista é recusado.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from app import cors_config

PROD_ORIGINS = "https://app.musclefit.com.br,https://zepp-os.zepp.com"
DEV_ORIGINS = "http://localhost:3000,http://devbox.tailnet-exemplo.ts.net:3000,http://100.64.0.10:3000"


def _client(monkeypatch, origins: str) -> TestClient:
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", origins)
    app = FastAPI()

    @app.post("/auth/login")
    def login():
        return {"ok": True}

    @app.get("/auth/me")
    def me():
        return {"ok": True}

    app.add_middleware(CORSMiddleware, **cors_config.cors_middleware_kwargs())
    return TestClient(app)


def _preflight(client: TestClient, origin: str, method: str = "POST", headers: str = "content-type"):
    return client.options(
        "/auth/login",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": headers,
        },
    )


@pytest.mark.parametrize(
    "origins,origin",
    [
        (PROD_ORIGINS, "https://app.musclefit.com.br"),
        (PROD_ORIGINS, "https://zepp-os.zepp.com"),
        (DEV_ORIGINS, "http://localhost:3000"),
        (DEV_ORIGINS, "http://devbox.tailnet-exemplo.ts.net:3000"),
        (DEV_ORIGINS, "http://100.64.0.10:3000"),
    ],
)
def test_preflight_de_origem_permitida_passa_com_credenciais(monkeypatch, origins, origin):
    client = _client(monkeypatch, origins)
    for method in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        resp = _preflight(client, origin, method=method, headers="content-type,x-api-token")
        assert resp.status_code == 200, (method, resp.text)
        assert resp.headers["access-control-allow-origin"] == origin
        assert resp.headers["access-control-allow-credentials"] == "true"
        allowed = {h.strip().lower() for h in resp.headers["access-control-allow-headers"].split(",")}
        assert {"content-type", "x-api-token"} <= allowed
        assert "*" not in resp.headers["access-control-allow-methods"]


def test_requisicao_simples_de_origem_permitida_recebe_acao(monkeypatch):
    client = _client(monkeypatch, PROD_ORIGINS)
    resp = client.get("/auth/me", headers={"Origin": "https://app.musclefit.com.br"})
    assert resp.headers["access-control-allow-origin"] == "https://app.musclefit.com.br"
    assert resp.headers["access-control-allow-credentials"] == "true"


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example",
        "https://musclefit.com.br.evil.example",
        "https://evil.musclefit.com.br",
        "http://app.musclefit.com.br",  # esquema diferente
        "null",
    ],
)
def test_origem_estranha_nao_recebe_acao(monkeypatch, origin):
    client = _client(monkeypatch, PROD_ORIGINS)
    resp = _preflight(client, origin)
    assert resp.status_code == 400
    assert "access-control-allow-origin" not in resp.headers

    simples = client.get("/auth/me", headers={"Origin": origin})
    assert "access-control-allow-origin" not in simples.headers


def test_header_ou_metodo_fora_da_lista_e_recusado(monkeypatch):
    client = _client(monkeypatch, PROD_ORIGINS)
    assert _preflight(client, "https://app.musclefit.com.br", headers="x-qualquer").status_code == 400
    assert _preflight(client, "https://app.musclefit.com.br", method="TRACE").status_code == 400


def test_lista_de_origens_descarta_curinga_vazio_e_barra_final():
    assert cors_config.cors_allowed_origins(" https://a.example/ ,, *, https://*.x.com, https://a.example") == [
        "https://a.example"
    ]


def test_default_sem_env_e_so_o_app_de_producao(monkeypatch):
    monkeypatch.delenv("CORS_ALLOWED_ORIGINS", raising=False)
    assert cors_config.cors_allowed_origins() == ["https://app.musclefit.com.br"]


def test_apps_reais_usam_a_config_explicita():
    from app.main import app as api_core
    from app.main_engines import app as api_engines

    for app in (api_core, api_engines):
        cors = [m for m in app.user_middleware if m.cls is CORSMiddleware]
        assert len(cors) == 1
        kwargs = cors[0].kwargs
        assert kwargs["allow_methods"] == list(cors_config.CORS_ALLOWED_METHODS)
        assert kwargs["allow_headers"] == list(cors_config.CORS_ALLOWED_HEADERS)
        assert kwargs["allow_credentials"] is True
        assert "*" not in kwargs["allow_origins"]
