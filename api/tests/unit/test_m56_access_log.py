"""M56: log de acesso da API (`app/access_log.py`) - uma linha JSON por
requisicao com metodo, TEMPLATE da rota, status e duracao; nada de corpo,
headers, query ou path cru."""

from __future__ import annotations

import json
import logging

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import access_log

SEGREDO = "segredo-que-nao-pode-ir-para-o-log"


class _Coletor(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.INFO)
        self.linhas: list[str] = []

    def emit(self, record):
        self.linhas.append(record.getMessage())


@pytest.fixture
def eventos():
    coletor = _Coletor()
    access_log.logger.addHandler(coletor)
    try:
        yield coletor.linhas
    finally:
        access_log.logger.removeHandler(coletor)


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()

    @app.get("/planos/{plan_id}")
    def plano(plan_id: int):
        return {"id": plan_id}

    @app.post("/planos")
    def criar():
        return {"ok": True}

    @app.get("/indisponivel")
    def indisponivel():
        raise HTTPException(status_code=503, detail="fora")

    @app.get("/quebra")
    def quebra():
        raise RuntimeError("bug")

    app.add_middleware(access_log.AccessLogMiddleware)
    return TestClient(app, raise_server_exceptions=False)


def _unico(eventos: list[str]) -> dict:
    assert len(eventos) == 1, eventos
    return json.loads(eventos[0])


def test_sucesso_loga_template_status_e_duracao(client, eventos):
    resposta = client.get(f"/planos/42?token={SEGREDO}", headers={"X-API-Token": SEGREDO, "Cookie": f"musclefit_session={SEGREDO}"})
    assert resposta.status_code == 200

    evento = _unico(eventos)
    assert evento["event"] == "http_access"
    assert evento["method"] == "GET"
    assert evento["route"] == "/planos/{plan_id}"
    assert evento["status"] == 200
    assert isinstance(evento["duration_ms"], (int, float)) and evento["duration_ms"] >= 0
    assert set(evento) == {"event", "method", "route", "status", "duration_ms"}
    assert SEGREDO not in eventos[0]
    assert "42" not in evento["route"]


def test_post_nao_loga_corpo(client, eventos):
    client.post("/planos", json={"senha": SEGREDO})
    evento = _unico(eventos)
    assert (evento["method"], evento["route"], evento["status"]) == ("POST", "/planos", 200)
    assert SEGREDO not in eventos[0]


def test_http_exception_loga_o_status_enviado(client, eventos):
    assert client.get("/indisponivel").status_code == 503
    assert _unico(eventos)["status"] == 503


def test_excecao_nao_tratada_loga_500(client, eventos):
    assert client.get("/quebra").status_code == 500
    evento = _unico(eventos)
    assert (evento["route"], evento["status"]) == ("/quebra", 500)


def test_rota_inexistente_nao_loga_o_path_cru(client, eventos):
    assert client.get(f"/nao-existe/{SEGREDO}").status_code == 404
    evento = _unico(eventos)
    assert (evento["route"], evento["status"]) == (access_log.UNMATCHED_ROUTE, 404)
    assert SEGREDO not in eventos[0]


def test_logger_tem_saida_propria_em_stdout_sem_propagar():
    # Atras do Lambda Web Adapter o logger raiz nao tem handler: sem saida
    # propria um INFO sumiria (ver docstring do modulo).
    assert access_log.logger.propagate is False
    assert access_log.logger.level == logging.INFO
    assert any(getattr(h, "_access_log", False) for h in access_log.logger.handlers)


def test_app_principal_registra_o_middleware():
    from app.main import app

    assert any(m.cls is access_log.AccessLogMiddleware for m in app.user_middleware)
