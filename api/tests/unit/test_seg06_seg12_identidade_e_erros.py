"""SEG-06: as rotas que resolvem identidade por `security.
resolve_identity_from_credentials` (catalogo, gyms, equipment_exercises,
muscle_volume_classes) aplicam a mesma trava de `must_change_password` de
`get_current_identity`, com o mesmo 403 `{"detail", "code"}` - tambem no app
de motores. SEG-12: respostas 5xx nao carregam o texto da excecao.

Rodar: cd api && python -m pytest tests/unit/test_seg06_seg12_identidade_e_erros.py
"""

from __future__ import annotations

import importlib
import logging

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app import db_helpers, security
from app.db import DataApiError
from app.security import (
    MUST_CHANGE_PASSWORD_CODE,
    MUST_CHANGE_PASSWORD_DETAIL,
    Identity,
    MustChangePasswordRequired,
    get_current_identity,
)
from tests.support.fake_db import RaisingCursor

SEGREDO = "SELECT segredo FROM tabela_interna_xyz"

PENDENTE = Identity(user_id=7, role="regular", source="session", must_change_password=True)
NORMAL = Identity(user_id=7, role="regular", source="session")
MESTRE = Identity(user_id=1, role="admin", source="master_token")

# (modulo cujo `_connect` a rota usa, rota GET protegida)
ROTAS = [
    ("gyms", "/gyms"),
    ("exercise_catalog_admin", "/exercises"),
    ("exercise_mapping_catalog", "/exercise-mapping-catalog"),
    ("equipment_exercises", "/equipment-exercises"),
    ("muscle_volume_classes", "/muscle-volume-classes"),
]


class _PassouDaAutenticacao(Exception):
    pass


def _chegou_no_banco():
    raise _PassouDaAutenticacao()


@pytest.fixture(scope="module")
def api_core():
    from app.main import app

    return TestClient(app)


# ---------------------------------------------------------------- SEG-06


@pytest.mark.parametrize("identidade", [NORMAL, MESTRE])
def test_credenciais_sem_pendencia_devolvem_a_identidade(monkeypatch, identidade):
    monkeypatch.setattr(security, "resolve_identity", lambda token: identidade)
    assert security.resolve_identity_from_credentials("tok", None) is identidade


def test_credenciais_com_troca_pendente_dao_403(monkeypatch):
    monkeypatch.setattr(security, "resolve_identity", lambda token: PENDENTE)
    with pytest.raises(MustChangePasswordRequired) as erro:
        security.resolve_identity_from_credentials(None, "cookie")
    assert erro.value.status_code == 403
    assert erro.value.detail == MUST_CHANGE_PASSWORD_DETAIL


@pytest.mark.parametrize("modulo, rota", ROTAS)
def test_rota_bloqueia_sessao_com_troca_pendente(monkeypatch, api_core, modulo, rota):
    monkeypatch.setattr(security, "resolve_identity", lambda token: PENDENTE)
    monkeypatch.setattr(importlib.import_module(f"app.{modulo}"), "_connect", _chegou_no_banco)
    resp = api_core.get(rota, headers={"X-API-Token": "tok"})
    assert resp.status_code == 403
    assert resp.json() == {"detail": MUST_CHANGE_PASSWORD_DETAIL, "code": MUST_CHANGE_PASSWORD_CODE}


@pytest.mark.parametrize("identidade", [NORMAL, MESTRE])
@pytest.mark.parametrize("modulo, rota", ROTAS)
def test_rota_segue_igual_para_usuario_normal_e_token_mestre(monkeypatch, api_core, modulo, rota, identidade):
    monkeypatch.setattr(security, "resolve_identity", lambda token: identidade)
    monkeypatch.setattr(importlib.import_module(f"app.{modulo}"), "_connect", _chegou_no_banco)
    # Passou da identidade e foi ao banco, como antes.
    with pytest.raises(_PassouDaAutenticacao):
        api_core.get(rota, headers={"X-API-Token": "tok"})


def test_nenhuma_rota_desses_routers_esta_nas_excecoes_da_troca():
    """`resolve_identity_from_credentials` nao recebe o `Request` e por isso
    nao consulta `_MUST_CHANGE_EXEMPT`: correto enquanto nenhuma rota desses
    routers estiver la."""
    isentos = {caminho for _metodo, caminho in security._MUST_CHANGE_EXEMPT}
    vistos = 0
    for modulo, _rota in ROTAS:
        mod = importlib.import_module(f"app.{modulo}")
        for nome in dir(mod):
            if not nome.endswith("router"):
                continue
            for route in getattr(getattr(mod, nome), "routes", []):
                if isinstance(route, APIRoute):
                    vistos += 1
                    assert (route.path.rstrip("/") or "/") not in isentos, (modulo, route.path)
    assert vistos > 0


def test_app_de_motores_devolve_o_mesmo_corpo_do_403(monkeypatch):
    from app.main_engines import app as engines

    monkeypatch.setattr(security, "resolve_identity", lambda token: PENDENTE)
    resp = TestClient(engines).get("/analytics/health", headers={"X-API-Token": "tok"})
    assert resp.status_code == 403
    assert resp.json() == {"detail": MUST_CHANGE_PASSWORD_DETAIL, "code": MUST_CHANGE_PASSWORD_CODE}


# ---------------------------------------------------------------- SEG-12


def _db_fora():
    raise DataApiError(SEGREDO)


def _logou_o_segredo(caplog) -> bool:
    return any(SEGREDO in (r.exc_text or "") or SEGREDO in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("mensagem", [None, db_helpers.DB_UNAVAILABLE_MYSQL])
def test_connect_or_503_nao_expoe_a_excecao_e_loga(caplog, mensagem):
    with caplog.at_level(logging.ERROR), pytest.raises(HTTPException) as erro:
        db_helpers.connect_or_503(_db_fora, mensagem)
    assert erro.value.status_code == 503
    assert erro.value.detail == {
        "error": "database_unavailable",
        "message": mensagem or db_helpers.DB_UNAVAILABLE_GENERIC,
    }
    assert SEGREDO not in str(erro.value.detail)
    assert _logou_o_segredo(caplog)


def test_insights_connect_nao_expoe_a_excecao(monkeypatch, caplog):
    from app import insights_core

    monkeypatch.setattr(insights_core, "get_conn", _db_fora)
    with caplog.at_level(logging.ERROR), pytest.raises(HTTPException) as erro:
        insights_core._connect()
    assert erro.value.status_code == 503
    assert erro.value.detail == "Não foi possível conectar ao banco para montar o pacote de dados."
    assert _logou_o_segredo(caplog)


@pytest.mark.parametrize("nome", ["progression", "equipment_exercise_preferences"])
def test_tabela_de_migracao_pendente_nao_expoe_a_excecao(caplog, nome):
    modulo = importlib.import_module(f"app.{nome}")
    with caplog.at_level(logging.ERROR):
        erro = modulo._unavailable(RuntimeError(SEGREDO))
    assert erro.status_code == 503
    assert list(erro.detail) == ["error", "message"]
    assert SEGREDO not in str(erro.detail)
    assert _logou_o_segredo(caplog)


class _BoomConn:
    def cursor(self):
        return RaisingCursor(lambda: RuntimeError(SEGREDO))

    def rollback(self):
        pass

    def commit(self):
        pass

    def close(self):
        pass


@pytest.mark.parametrize(
    "nome, metodo, rota",
    [("effort_metrics", "get", "/effort-metrics"), ("sleep_metrics", "post", "/sleep-metrics")],
)
def test_metricas_respondem_500_generico(monkeypatch, caplog, nome, metodo, rota):
    modulo = importlib.import_module(f"app.{nome}")
    monkeypatch.setattr(modulo, "get_conn", lambda: _BoomConn())
    app = FastAPI()
    app.include_router(modulo.router)
    app.dependency_overrides[get_current_identity] = lambda: NORMAL
    client = TestClient(app, raise_server_exceptions=False)

    with caplog.at_level(logging.ERROR):
        if metodo == "post":
            resp = client.post(rota, json={"local_date": "2026-10-09"})
        else:
            resp = client.get(rota)

    assert resp.status_code == 500, resp.text
    assert SEGREDO not in resp.text
    assert resp.json()["detail"] == "Erro interno ao processar a solicitação."
    assert _logou_o_segredo(caplog)


def test_intervals_5xx_generico_e_4xx_mantem_a_mensagem(caplog):
    from app import intervals_integration as ii
    from app.intervals_client import IntervalsClientError

    with caplog.at_level(logging.ERROR):
        erro_5xx = ii._intervals_client_http_error(IntervalsClientError(f"HTTP 500: {SEGREDO}", status_code=500))
        erro_rede = ii._intervals_client_http_error(IntervalsClientError(f"Falha de rede: {SEGREDO}"))
    assert (erro_5xx.status_code, erro_5xx.detail) == (500, ii.INTERVALS_UPSTREAM_ERROR_DETAIL)
    assert (erro_rede.status_code, erro_rede.detail) == (502, ii.INTERVALS_UPSTREAM_ERROR_DETAIL)
    assert _logou_o_segredo(caplog)

    mensagem_401 = "Chave de API do Intervals.icu inválida ou expirada."
    chave = ii._intervals_client_http_error(IntervalsClientError(mensagem_401, status_code=401))
    # 401 do upstream nao pode chegar ao frontend como 401 (deslogaria o usuario).
    assert (chave.status_code, chave.detail) == (400, mensagem_401)


def _explode(*_args, **_kwargs):
    raise RuntimeError(SEGREDO)


SLOT_EQUIPAMENTO = {"items": [{"item_kind": "equipment", "muscle_group_id": 1}]}
ARGS_MODO_B = dict(
    template={}, slot=SLOT_EQUIPAMENTO, gym_id=None, gym_name=None, plan_date="2026-10-09",
    user_id=1, min_rest_seconds=60, max_rest_seconds=90,
)


def test_geracao_legada_modo_b_nao_expoe_a_excecao(monkeypatch, caplog):
    from app.generation import mode_b_legacy
    from app.workout_plan_ai import WorkoutPlanGenerationError

    monkeypatch.setattr(mode_b_legacy, "_load_muscle_group_names", _explode)
    with caplog.at_level(logging.ERROR), pytest.raises(WorkoutPlanGenerationError) as erro:
        mode_b_legacy.generate_plan_draft_from_equipment_template(**ARGS_MODO_B)
    assert SEGREDO not in str(erro.value)
    assert _logou_o_segredo(caplog)


def test_geracao_deterministica_modo_b_nao_expoe_a_excecao(monkeypatch, caplog):
    from app import generation, workout_plan_ai
    from app.generation import mode_b

    monkeypatch.setattr(generation, "deterministic_enabled", lambda: True)
    monkeypatch.setattr(workout_plan_ai, "gather_ficha_context", _explode)
    with caplog.at_level(logging.ERROR), pytest.raises(workout_plan_ai.WorkoutPlanGenerationError) as erro:
        mode_b.generate_plan_draft_from_equipment_template(**ARGS_MODO_B)
    assert SEGREDO not in str(erro.value)
    assert _logou_o_segredo(caplog)


def test_geracao_deterministica_modo_a_nao_expoe_a_excecao(monkeypatch, caplog):
    from app import generation, workout_plan_ai
    from app.generation import mode_a
    from app.intelligence import template_volume_planner

    monkeypatch.setattr(generation, "deterministic_enabled", lambda: True)
    monkeypatch.setattr(template_volume_planner, "build_template_cycle_context", _explode)
    with caplog.at_level(logging.ERROR), pytest.raises(workout_plan_ai.WorkoutPlanGenerationError) as erro:
        mode_a.generate_plan_draft_from_template_a(
            template={}, slot={"id": 1}, gym_id=None, gym_name=None, plan_date="2026-10-09", user_id=1
        )
    assert SEGREDO not in str(erro.value)
    assert _logou_o_segredo(caplog)
