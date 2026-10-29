"""SEG-12: /docs, /redoc e /openapi.json so com API_DOCS_ENABLED=1 e `detail`
de erro sem a mensagem crua da excecao (ela vai para o log).

Rodar: cd api && python -m pytest tests/unit/test_seg12_docs_e_erros_genericos.py
"""

from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import api_docs, body_composition
from app.security import Identity, get_current_identity
from tests.support.fake_db import RaisingCursor

SEGREDO = "tabela_interna_xyz nao existe"


def _app_com_docs() -> FastAPI:
    app = FastAPI(**api_docs.api_docs_kwargs())

    @app.get("/health")
    def health():
        return {"status": "ok"}

    return app


def test_sem_a_env_a_documentacao_fica_fora(monkeypatch):
    monkeypatch.delenv("API_DOCS_ENABLED", raising=False)
    client = TestClient(_app_com_docs())
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404, path
    # readiness do Lambda Web Adapter nao pode ser afetado
    assert client.get("/health").status_code == 200


@pytest.mark.parametrize("valor", ["0", "true", ""])
def test_somente_o_valor_1_liga_a_documentacao(monkeypatch, valor):
    monkeypatch.setenv("API_DOCS_ENABLED", valor)
    assert api_docs.api_docs_enabled() is False


def test_com_a_env_1_a_documentacao_responde(monkeypatch):
    monkeypatch.setenv("API_DOCS_ENABLED", "1")
    client = TestClient(_app_com_docs())
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 200, path


def test_apps_reais_usam_o_helper(monkeypatch):
    monkeypatch.delenv("API_DOCS_ENABLED", raising=False)
    # Os apps sao criados no import; em testes a env nao esta definida.
    from app.main import app as api_core
    from app.main_engines import app as api_engines

    for app in (api_core, api_engines):
        assert app.docs_url is None
        assert app.redoc_url is None
        assert app.openapi_url is None


class _BoomConn:
    rolled_back = False

    def cursor(self):
        return RaisingCursor(lambda: RuntimeError(SEGREDO))

    def rollback(self):
        self.rolled_back = True

    def commit(self):
        pass

    def close(self):
        pass


def test_erro_interno_devolve_500_generico_e_loga_a_excecao(monkeypatch, caplog):
    monkeypatch.setattr(body_composition, "get_conn", lambda: _BoomConn())
    app = FastAPI()
    app.include_router(body_composition.router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(user_id=1, role="regular", source="session")
    client = TestClient(app, raise_server_exceptions=False)

    with caplog.at_level(logging.ERROR):
        resp = client.put("/body-composition/5", json={"weight_kg": 80})

    assert resp.status_code == 500
    assert SEGREDO not in resp.text
    assert resp.json()["detail"] == "Erro interno ao processar a solicitação."
    assert any(SEGREDO in (r.exc_text or "") or SEGREDO in r.getMessage() for r in caplog.records)
