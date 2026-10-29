"""Worker da leitura da refeição por IA (`app/nutrition_meal_parse_worker.py`,
L5b), o despacho da Lambda `musclefit-workout-ai` e o sweep do `GET /jobs`.

O `ai_usage_log` fecha em TODOS os caminhos: sucesso (modelo e tokens), falha
da IA (código da OpenAI), falha da busca depois da IA (com os tokens gastos),
texto inválido e falha do handler antes do despacho. A busca no catálogo é a
TACO real em memória (`tests/support/meal_parse_eval.py`)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi import HTTPException

from app import async_jobs, lambda_workout_ai, nutrition_meal_parse_worker as worker
from app import workout_ai_generation_core as core
from app.domain.nutrition import meal_parse as mp
from app.domain.nutrition.ai_usage import MEAL_PARSE_JOB_TYPE, AiUsageStatus
from app.openai_client import OPENAI_TIMEOUT, USER_MESSAGE_TIMEOUT, OpenAIGenerationError, StructuredResult
from tests.support import meal_parse_eval

PAYLOAD = {"text": "arroz branco e whey", "meal_slot": "lunch", "local_date": "2026-10-18", "diary_user_id": 2,
           "job_id": "job-1"}
SAIDA_DA_IA = {
    "items": [
        {"name": "arroz branco", "quantity": None, "unit": "g", "grams_estimate": 120, "preparation": "cozido",
         "confidence": 0.9, mp.ESTIMATE_KEY: None},
        {"name": "whey protein", "quantity": 30, "unit": "g", "grams_estimate": 30, "preparation": None,
         "confidence": 0.95, mp.ESTIMATE_KEY: {"energy_kcal": 400, "protein_g": 80, "carbohydrates_g": 10,
                                                "total_fat_g": 5, "fiber_g": None}},
    ],
    "notes": None,
}


@pytest.fixture
def fechamentos(monkeypatch) -> list[tuple[Any, dict]]:
    registro: list[tuple[Any, dict]] = []
    monkeypatch.setattr(worker.quota, "finish_usage", lambda usage_id, **k: registro.append((usage_id, k)) or True)
    monkeypatch.setattr(worker, "transaction", lambda: meal_parse_eval._Conexao())
    monkeypatch.setattr(worker, "require_food_tables", lambda cur: None)
    return registro


def _ia(monkeypatch, saida: Any = SAIDA_DA_IA, erro: Exception | None = None) -> list[dict]:
    chamadas: list[dict] = []

    def _parse(texto, slot, *, user_id=None):
        chamadas.append({"texto": texto, "slot": slot, "user_id": user_id})
        if erro is not None:
            raise erro
        return StructuredResult(data=saida, model="gpt-5.4-mini-2026-03-17", tokens_in=1100, tokens_out=260)

    monkeypatch.setattr(worker.nutrition_meal_parse_ai, "parse_meal_text", _parse)
    return chamadas


def test_sucesso_fecha_com_modelo_e_tokens_e_devolve_a_decisao_do_motor(monkeypatch, fechamentos):
    chamadas = _ia(monkeypatch)
    resultado = worker.dispatch_meal_parse(dict(PAYLOAD), 1, 77)
    assert chamadas == [{"texto": "arroz branco e whey", "slot": "lunch", "user_id": 1}]
    assert fechamentos == [(77, {"status": AiUsageStatus.SUCCEEDED, "model": "gpt-5.4-mini-2026-03-17",
                                 "tokens_in": 1100, "tokens_out": 260})]
    arroz, whey = resultado["items"]
    assert arroz["status"] == "selected" and arroz["food_id"] == 3 and arroz["quantity"] == 120.0
    assert arroz["candidates"][0]["food_id"] == 3 and arroz["candidates"][0]["basis"] == "g"
    # Quantidade não dita: os gramas da IA, com o aviso do dono.
    assert [lim["code"] for lim in arroz["limitations"]] == [mp.LIMIT_QUANTITY_FROM_GRAMS]
    assert whey["status"] == "estimated" and whey["candidate_for_catalog"] is True
    assert {lim["code"] for lim in whey["limitations"]} == {mp.LIMIT_AI_ESTIMATE}
    assert all(lim["label"] for lim in whey["limitations"])  # rótulo pronto do dono
    assert resultado["user_id"] == 2 and resultado["prompt_version"] == worker.nutrition_meal_parse_ai.PROMPT_VERSION
    assert resultado["model"] == "gpt-5.4-mini-2026-03-17" and resultado["local_date"] == "2026-10-18"
    assert resultado["job_id"] == "job-1"
    json.dumps(resultado)  # vai para o DynamoDB


def test_falha_da_ia_fecha_como_failed_com_o_codigo_e_mensagem_generica(monkeypatch, fechamentos):
    _ia(monkeypatch, erro=OpenAIGenerationError(USER_MESSAGE_TIMEOUT, code=OPENAI_TIMEOUT))
    with pytest.raises(worker.MealParseError) as exc:
        worker.dispatch_meal_parse(dict(PAYLOAD), 1, 77)
    assert exc.value.detail == USER_MESSAGE_TIMEOUT
    [(usage_id, campos)] = fechamentos
    assert usage_id == 77 and campos["status"] is AiUsageStatus.FAILED and campos["error_code"] == OPENAI_TIMEOUT
    assert "tokens_in" not in campos


def test_erro_inesperado_na_ia_nao_vaza_detalhe(monkeypatch, fechamentos):
    _ia(monkeypatch, erro=KeyError("segredo interno"))
    with pytest.raises(worker.MealParseError) as exc:
        worker.dispatch_meal_parse(dict(PAYLOAD), 1, 77)
    assert "segredo" not in exc.value.detail
    assert fechamentos[0][1]["error_code"] == mp.ERR_PARSE_FAILED


def test_falha_da_busca_depois_da_ia_fecha_com_os_tokens_gastos(monkeypatch, fechamentos):
    _ia(monkeypatch)

    def _sem_catalogo(cur):
        raise HTTPException(status_code=503, detail="sem 080")

    monkeypatch.setattr(worker, "require_food_tables", _sem_catalogo)
    with pytest.raises(worker.MealParseError):
        worker.dispatch_meal_parse(dict(PAYLOAD), 1, 77)
    [(usage_id, campos)] = fechamentos
    assert campos == {"status": AiUsageStatus.FAILED, "error_code": mp.ERR_MATCH_FAILED,
                      "model": "gpt-5.4-mini-2026-03-17", "tokens_in": 1100, "tokens_out": 260}


def test_texto_invalido_fecha_sem_chamar_a_ia(monkeypatch, fechamentos):
    chamadas = _ia(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        worker.dispatch_meal_parse({**PAYLOAD, "text": "  "}, 1, 77)
    assert exc.value.status_code == 400 and chamadas == []
    assert fechamentos == [(77, {"status": AiUsageStatus.FAILED, "error_code": mp.ERR_TEXT_EMPTY})]


def test_leitura_sem_item_nao_busca(monkeypatch, fechamentos):
    _ia(monkeypatch, saida={"items": [], "notes": "Sem comida."})
    monkeypatch.setattr(worker, "transaction", lambda: (_ for _ in ()).throw(AssertionError("não devia buscar")))
    resultado = worker.dispatch_meal_parse(dict(PAYLOAD), 1, 77)
    assert resultado["items"] == [] and [lim["code"] for lim in resultado["limitations"]] == [mp.LIMIT_NO_ITEMS]
    assert fechamentos[0][1]["status"] is AiUsageStatus.SUCCEEDED


def test_busca_no_diario_do_aluno_com_e_sem_o_preparo(monkeypatch, fechamentos):
    buscas: list[str] = []
    original = worker.search_candidates

    def _espia(cur, consulta, **k):
        buscas.append(consulta)
        assert k == {"registrant_user_id": 1, "diary_user_id": 2}
        return original(cur, consulta, **k)

    monkeypatch.setattr(worker, "search_candidates", _espia)
    _ia(monkeypatch)
    worker.dispatch_meal_parse(dict(PAYLOAD), 1, 77)
    assert buscas == ["arroz branco cozido", "arroz branco", "whey protein"]


# ---------------------------------------------------------------------------
# Despacho e handler da Lambda
# ---------------------------------------------------------------------------


def test_despacho_da_lambda_passa_o_ai_usage_id(monkeypatch):
    recebido = []
    monkeypatch.setattr(worker, "dispatch_meal_parse", lambda payload, user_id, uid: recebido.append((payload, user_id, uid)) or {"ok": 1})
    assert core.dispatch_workout_ai_generation(MEAL_PARSE_JOB_TYPE, {"text": "x"}, 1, ai_usage_id=9) == {"ok": 1}
    assert recebido == [({"text": "x"}, 1, 9)]


def _evento(**extra) -> dict:
    return {"job_id": "job-1", "job_type": MEAL_PARSE_JOB_TYPE, "payload": dict(PAYLOAD), "user_id": 1,
            "ai_usage_id": 77, **extra}


@pytest.fixture
def handler_spy(monkeypatch):
    registro: dict = {"finish": [], "failed": [], "done": []}
    monkeypatch.setattr(lambda_workout_ai.nutrition_ai_quota, "finish_usage",
                        lambda usage_id, **k: registro["finish"].append((usage_id, k)) or True)
    monkeypatch.setattr(async_jobs, "mark_failed", lambda job_id, error: registro["failed"].append(error) or True)
    monkeypatch.setattr(async_jobs, "mark_done", lambda job_id, result: registro["done"].append(result) or True)
    return registro


def test_handler_que_falha_antes_do_despacho_fecha_a_linha(monkeypatch, handler_spy):
    def _claim(job_id):
        raise RuntimeError("DynamoDB fora")

    monkeypatch.setattr(async_jobs, "claim_job", _claim)
    lambda_workout_ai.handler(_evento(), None)
    assert handler_spy["finish"] == [(77, {"status": AiUsageStatus.FAILED, "error_code": mp.ERR_WORKER_FAILED})]
    assert handler_spy["failed"] == ["DynamoDB fora"]


def test_handler_nao_fecha_de_novo_o_que_o_worker_fechou(monkeypatch, handler_spy):
    monkeypatch.setattr(async_jobs, "claim_job", lambda job_id: True)

    def _worker_falhou(*a, **k):
        raise worker.MealParseError("A IA demorou demais para responder. Tente novamente.")

    monkeypatch.setattr(lambda_workout_ai, "dispatch_workout_ai_generation", _worker_falhou)
    lambda_workout_ai.handler(_evento(), None)
    assert handler_spy["finish"] == []
    assert handler_spy["failed"] == ["A IA demorou demais para responder. Tente novamente."]


def test_entrega_duplicada_nao_roda_nem_fecha(monkeypatch, handler_spy):
    monkeypatch.setattr(async_jobs, "claim_job", lambda job_id: False)
    lambda_workout_ai.handler(_evento(), None)
    assert handler_spy == {"finish": [], "failed": [], "done": []}


def test_job_sem_ai_usage_id_segue_como_antes(monkeypatch, handler_spy):
    monkeypatch.setattr(async_jobs, "claim_job", lambda job_id: (_ for _ in ()).throw(RuntimeError("x")))
    lambda_workout_ai.handler({"job_id": "j", "job_type": "insights", "payload": {}, "user_id": 1}, None)
    assert handler_spy["finish"] == [] and handler_spy["failed"] == ["x"]


# ---------------------------------------------------------------------------
# GET /jobs: worker perdido da leitura fecha as linhas vencidas
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("job_type", "codigo", "varre"), [
    (MEAL_PARSE_JOB_TYPE, async_jobs.WORKER_LOST_ERROR_CODE, True),
    (MEAL_PARSE_JOB_TYPE, None, False),
    ("insights", async_jobs.WORKER_LOST_ERROR_CODE, False),
])
def test_get_jobs_worker_lost_da_nutricao_dispara_o_sweep(monkeypatch, job_type, codigo, varre):
    from app import jobs
    from app.security import Identity

    job = {"job_id": "j", "job_type": job_type, "status": "failed", **({"error_code": codigo} if codigo else {})}
    monkeypatch.setattr(jobs.async_jobs, "get_job", lambda job_id, owner_user_id: job)
    varridas = []
    monkeypatch.setattr(jobs, "_sweep_nutrition_ai_usage", lambda: varridas.append(1))
    assert jobs.get_job_status("j", Identity(user_id=1, role="regular", source="session")) == job
    assert bool(varridas) is varre
