"""SEG-12 (follow-up do #465): falha da OpenAI nunca leva o corpo de erro da
OpenAI ao usuario - nem no `detail` do 503, nem no `error` do job assincrono,
nem no erro do card. A mensagem e generica em pt-BR (por tipo); o
diagnostico (status, corpo truncado, request id) vai so para o log, no
evento `llm.error`, sem a chave.

Rede sempre mockada: nenhuma chamada real a OpenAI.

Rodar: cd api && python -m pytest tests/unit/test_seg12_erro_openai_sem_vazamento.py
"""

from __future__ import annotations

import io
import json
import logging
import urllib.error

import pytest
from fastapi import HTTPException

from app import lambda_workout_ai, openai_client, workouts_analysis_core

CHAVE = "sk-proj-CHAVEDETESTE1234567890"
CORPO_OPENAI = "CORPO-INTERNO-DA-OPENAI org-xyz quota detalhada"
REQUEST_ID = "req_abc123"


def _http_error(status: int, corpo: str):
    corpo_json = json.dumps({"error": {"code": "server_error", "message": f"{corpo} chave {CHAVE}"}})
    return urllib.error.HTTPError(
        openai_client._RESPONSES_URL,
        status,
        "erro",
        {"x-request-id": REQUEST_ID},
        io.BytesIO(corpo_json.encode("utf-8")),
    )


@pytest.fixture
def openai_falha(monkeypatch):
    """`urlopen` devolve o erro da vez; sem espera real entre tentativas."""
    monkeypatch.setenv("OPENAI_API_KEY", CHAVE)
    monkeypatch.setattr(openai_client, "_sleep", lambda s: None)
    estado = {"status": 500}

    def _falha(request, timeout=None):
        raise _http_error(estado["status"], CORPO_OPENAI)

    monkeypatch.setattr(openai_client.urllib.request, "urlopen", _falha)
    return estado


def _eventos(caplog, nome):
    saida = []
    for rec in caplog.records:
        if rec.name == "musclefit.events":
            payload = json.loads(rec.getMessage())
            if payload.get("event") == nome:
                saida.append(payload)
    return saida


@pytest.mark.parametrize(
    "status, mensagem",
    [
        (500, openai_client.USER_MESSAGE_UNAVAILABLE),
        (401, openai_client.USER_MESSAGE_UNAVAILABLE),
        (429, openai_client.USER_MESSAGE_RATE_LIMIT),
        (408, openai_client.USER_MESSAGE_TIMEOUT),
    ],
)
def test_mensagem_generica_por_tipo_e_diagnostico_no_log(openai_falha, caplog, status, mensagem):
    openai_falha["status"] = status
    with caplog.at_level(logging.INFO, logger="musclefit.events"):
        with pytest.raises(openai_client.OpenAIGenerationError) as exc:
            openai_client.call_openai("PROMPT-DO-USUARIO", "m", feature="insights", user_id=7)

    assert str(exc.value) == mensagem
    assert CORPO_OPENAI not in str(exc.value)
    assert exc.value.status == status

    erros = _eventos(caplog, openai_client.EVENT_LLM_ERROR)
    assert erros, "o diagnostico precisa ir para o log"
    ultimo = erros[-1]
    assert ultimo["status"] == status
    assert ultimo["request_id"] == REQUEST_ID
    assert ultimo["feature"] == "insights" and ultimo["user_id"] == 7
    assert ultimo["will_retry"] is False
    assert CORPO_OPENAI in ultimo["diagnostic"]
    assert len(ultimo["diagnostic"]) <= openai_client.DIAGNOSTIC_MAX_CHARS
    # Nem a chave nem o prompt chegam ao log.
    assert CHAVE not in caplog.text
    assert "PROMPT-DO-USUARIO" not in caplog.text


def test_detail_do_503_nao_tem_o_corpo_da_openai(openai_falha, monkeypatch, caplog):
    class _Agregador:
        def build_training_evolution_payload(self, days, user_id):
            return {}

    class _Rag:
        def build_rag_context(self, training):
            return ""

    monkeypatch.setattr(workouts_analysis_core, "DashboardAggregatorService", _Agregador)
    monkeypatch.setattr(workouts_analysis_core, "ReportDataService", _Rag)

    with caplog.at_level(logging.INFO, logger="musclefit.events"):
        with pytest.raises(HTTPException) as exc:
            workouts_analysis_core.dispatch_workouts_analysis({"days": 30}, user_id=7)

    assert exc.value.status_code == 503
    assert exc.value.detail == openai_client.USER_MESSAGE_UNAVAILABLE
    assert CORPO_OPENAI not in str(exc.value.detail)
    assert any(CORPO_OPENAI in e["diagnostic"] for e in _eventos(caplog, openai_client.EVENT_LLM_ERROR))


def test_erro_do_job_assincrono_nao_tem_o_corpo_da_openai(openai_falha, monkeypatch, caplog):
    gravados: list[tuple[str, str]] = []
    monkeypatch.setattr(lambda_workout_ai.async_jobs, "mark_failed", lambda job_id, msg: gravados.append((job_id, msg)))
    monkeypatch.setattr(lambda_workout_ai.async_jobs, "mark_done", lambda *a, **k: pytest.fail("nao deveria concluir"))
    monkeypatch.setattr(lambda_workout_ai.async_jobs, "claim_job", lambda job_id: True)  # M81

    def _dispatch(job_type, payload, user_id, ai_usage_id=None):
        return workouts_analysis_core.report_ai.analyze_training_evolution({}, 30, user_id=user_id)

    monkeypatch.setattr(lambda_workout_ai, "dispatch_workout_ai_generation", _dispatch)

    with caplog.at_level(logging.INFO):
        lambda_workout_ai.handler({"job_id": "job-1", "job_type": "workouts_analysis", "user_id": 7}, None)

    assert gravados == [("job-1", openai_client.USER_MESSAGE_UNAVAILABLE)]
    assert any(CORPO_OPENAI in e["diagnostic"] for e in _eventos(caplog, openai_client.EVENT_LLM_ERROR))
    assert CHAVE not in caplog.text


def test_erro_do_card_nao_tem_o_corpo_da_openai(monkeypatch, caplog):
    monkeypatch.setenv("OPENAI_API_KEY", CHAVE)

    def _falha(request, timeout=None):
        raise urllib.error.HTTPError(
            openai_client._IMAGES_URL,
            400,
            "erro",
            {"x-request-id": REQUEST_ID},
            io.BytesIO(json.dumps({"error": {"code": "moderation_blocked", "message": CORPO_OPENAI}}).encode()),
        )

    monkeypatch.setattr(openai_client.urllib.request, "urlopen", _falha)
    with caplog.at_level(logging.INFO, logger="musclefit.events"):
        with pytest.raises(openai_client.OpenAIGenerationError) as exc:
            openai_client.generate_image("prompt do card")

    # `exercise_card_jobs` grava `str(exc)` como `error_message` e `exc.code`
    # como `error_code` (que a tela traduz) - o codigo continua o mesmo.
    assert exc.value.code == openai_client.OPENAI_INVALID_REQUEST
    assert CORPO_OPENAI not in str(exc.value)
    [erro] = _eventos(caplog, openai_client.EVENT_LLM_ERROR)
    assert "moderation_blocked" in erro["diagnostic"] and CORPO_OPENAI in erro["diagnostic"]
    assert erro["feature"] == "exercise_card" and erro["request_id"] == REQUEST_ID


def test_diagnostico_mascara_chave_e_trunca():
    texto = openai_client.redact_diagnostic(f"Incorrect API key provided: {CHAVE}. Bearer abc.def " + "x" * 2000)
    assert CHAVE not in texto and "abc.def" not in texto
    assert len(texto) == openai_client.DIAGNOSTIC_MAX_CHARS


def test_mensagens_de_dominio_intencionais_nao_mudam():
    # Subclasses com mensagem propria (validacao de entrada) continuam iguais.
    exc = openai_client.OpenAIGenerationError("question é obrigatório.")
    assert str(exc) == "question é obrigatório." and exc.diagnostic is None
