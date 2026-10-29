"""SEG-04: token mestre comparado em tempo constante e geracao de ficha sem
token mestre nem URL publica.

1. `security`: o token mestre e comparado com `hmac.compare_digest`; valido
   -> 200, invalido/vazio/sem API_TOKEN -> 401 (rota por identidade e rota
   exclusiva do token mestre).
2. `workout_plan_ai.gather_ficha_context`: `gyms` e `exercise_catalog` saem
   do banco (mesmas funcoes de servico das rotas), sem `API_TOKEN` na env e
   sem rede, e reproduzem a fixture gravada com o codigo anterior (self-call
   HTTP com token mestre) - mesmo SQL, mesmos parametros, mesmo payload.
"""

from __future__ import annotations

import hmac
import http.client
import json
import socket
import urllib.request

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app import security, services, workout_plan_ai
from app.intelligence import muscle_recovery_service
from tests.support import plan_generator_context as pgc

MASTER = "token-mestre-de-teste-0123456789abcdefghijklmnop"


# ---------------------------------------------------------------------------
# 1. token mestre
# ---------------------------------------------------------------------------


@pytest.fixture
def auth_app(monkeypatch):
    monkeypatch.setenv("API_TOKEN", MASTER)
    monkeypatch.setattr(security, "_admin_user_id", lambda: 1)
    # M40: `resolve_identity` e `device_token_matches` passam por `_device_identity`.
    monkeypatch.setattr(security, "_device_identity", lambda token: None)
    monkeypatch.setattr(security, "_session_identity", lambda token: None)

    app = FastAPI()

    @app.get("/quem-sou")
    def quem_sou(identity: security.Identity = Depends(security.get_current_identity)):
        return {"user_id": identity.user_id, "source": identity.source}

    @app.get("/so-mestre", dependencies=[Depends(security.require_master_token)])
    def so_mestre():
        return {"ok": True}

    @app.get("/token-ou-dispositivo", dependencies=[Depends(security.require_token)])
    def token_ou_dispositivo():
        return {"ok": True}

    return TestClient(app)


@pytest.mark.parametrize("path", ["/quem-sou", "/so-mestre", "/token-ou-dispositivo"])
def test_token_mestre_valido_e_200(auth_app, path):
    response = auth_app.get(path, headers={"X-API-Token": MASTER})
    assert response.status_code == 200


def test_token_mestre_resolve_admin_como_master_token(auth_app):
    assert auth_app.get("/quem-sou", headers={"X-API-Token": MASTER}).json() == {
        "user_id": 1,
        "source": "master_token",
    }


@pytest.mark.parametrize("path", ["/quem-sou", "/so-mestre", "/token-ou-dispositivo"])
@pytest.mark.parametrize(
    "token",
    [
        MASTER[:-1] + "X",  # mesmo tamanho, ultimo caractere diferente
        MASTER + "a",  # prefixo correto e mais longo
        MASTER[:-1],  # prefixo correto e mais curto
        "",
    ],
)
def test_token_mestre_invalido_e_401(auth_app, path, token):
    response = auth_app.get(path, headers={"X-API-Token": token})
    assert response.status_code == 401


@pytest.mark.parametrize("path", ["/quem-sou", "/so-mestre", "/token-ou-dispositivo"])
def test_sem_header_e_401(auth_app, path):
    assert auth_app.get(path).status_code == 401


def test_sem_api_token_configurado_nenhum_token_vira_mestre(auth_app, monkeypatch):
    monkeypatch.setenv("API_TOKEN", "")
    assert auth_app.get("/quem-sou", headers={"X-API-Token": ""}).status_code == 401
    assert auth_app.get("/quem-sou", headers={"X-API-Token": MASTER}).status_code == 401


def test_comparacao_usa_hmac_compare_digest(monkeypatch):
    chamadas = []
    original = hmac.compare_digest

    def spy(a, b):
        chamadas.append((type(a), type(b)))
        return original(a, b)

    monkeypatch.setattr(security.hmac, "compare_digest", spy)
    assert security.master_token_matches(MASTER, MASTER) is True
    assert security.master_token_matches(MASTER[:-1] + "X", MASTER) is False
    assert chamadas == [(bytes, bytes), (bytes, bytes)]


@pytest.mark.parametrize("token, esperado", [(None, MASTER), ("", MASTER), (MASTER, ""), (MASTER, None)])
def test_vazio_de_qualquer_lado_nunca_casa(token, esperado):
    assert security.master_token_matches(token, esperado) is False


def test_token_nao_ascii_nao_quebra_a_comparacao():
    assert security.master_token_matches("tôken-çom-acento", MASTER) is False


# ---------------------------------------------------------------------------
# 2. plan-generator sem token mestre e sem rede
# ---------------------------------------------------------------------------


@pytest.fixture
def sem_rede(monkeypatch):
    def proibido(*_a, **_k):
        raise AssertionError("a geracao de ficha nao pode abrir conexao de rede")

    monkeypatch.setattr(urllib.request, "urlopen", proibido)
    monkeypatch.setattr(http.client.HTTPConnection, "connect", proibido)
    monkeypatch.setattr(socket, "create_connection", proibido)


@pytest.fixture
def banco_dublado(monkeypatch):
    conn = pgc.FakeConn()
    monkeypatch.setattr(workout_plan_ai, "transaction", pgc.fake_transaction_factory(conn))
    monkeypatch.setattr(services, "list_exercise_logs", lambda **_k: [])
    monkeypatch.setattr(services, "high_rpe_sets", lambda **_k: [])
    monkeypatch.setattr(
        muscle_recovery_service.MuscleRecoveryService, "get_recovery_overview", lambda self, **_k: []
    )
    return conn


def test_contexto_sem_api_token_e_sem_rede_reproduz_a_fixture_do_codigo_anterior(
    monkeypatch, sem_rede, banco_dublado
):
    monkeypatch.delenv("API_TOKEN", raising=False)
    monkeypatch.delenv("INTERNAL_API_BASE_URL", raising=False)
    fixture = json.loads(pgc.FIXTURE_PATH.read_text(encoding="utf-8"))

    context = workout_plan_ai.gather_ficha_context(None, 1)

    assert context["gyms"] == fixture["gyms"]
    assert context["exercise_catalog"] == fixture["exercise_catalog"]
    # byte a byte, como o prompt serializa (json.dumps)
    for key in ("gyms", "exercise_catalog"):
        assert json.dumps(context[key], ensure_ascii=False) == json.dumps(fixture[key], ensure_ascii=False)
    # mesmas consultas, na mesma ordem e com os mesmos parametros da rota
    assert banco_dublado.statements == fixture["statements"]


def test_modulo_nao_tem_mais_self_call_nem_url_interna():
    assert not hasattr(workout_plan_ai, "_internal_get")
    assert not hasattr(workout_plan_ai, "INTERNAL_API_BASE_URL")


def test_planner_sem_usuario_falha_explicito_em_vez_de_usar_o_token_mestre(sem_rede):
    from app.intelligence import template_volume_planner

    with pytest.raises(workout_plan_ai.WorkoutPlanGenerationError):
        template_volume_planner._load_projected_recovery("2026-10-10", None, user_id=None)
