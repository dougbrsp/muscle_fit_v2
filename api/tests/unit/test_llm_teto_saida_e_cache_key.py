"""Teto de saida (`max_output_tokens`), `prompt_cache_key` e tratamento de
resposta incompleta no cliente unico (`openai_client`). Rede sempre mockada;
nenhuma chamada paga."""

from __future__ import annotations

import json
import logging

import pytest

from app import openai_client, report_ai
from app.openai_client import (
    LLM_MAX_OUTPUT_TOKENS,
    LLM_OUTPUT_CAP_FLOOR_TOKENS,
    LLM_OUTPUT_CAP_HEADROOM_FACTOR,
    LLM_OUTPUT_OBSERVED_MAX_TOKENS,
    PROMPT_CACHE_KEY_FEATURES,
    OpenAIGenerationError,
    prompt_cache_key,
)
from app.domain.nutrition.ai_usage import AiFeature
from tests.support import llm_casos


class _Resposta:
    def __init__(self, data):
        self._data = data

    def read(self):
        return json.dumps(self._data).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def _chave(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "chave-de-teste")
    monkeypatch.setattr(openai_client, "_sleep", lambda s: None)


def _mock(monkeypatch, resposta):
    enviados = []

    def _fake(request, timeout=None):
        enviados.append(json.loads(request.data.decode("utf-8")))
        return _Resposta(resposta)

    monkeypatch.setattr(openai_client.urllib.request, "urlopen", _fake)
    return enviados


def _eventos(caplog, nome):
    saida = []
    for rec in caplog.records:
        if rec.name == "musclefit.events":
            payload = json.loads(rec.getMessage())
            if payload.get("event") == nome:
                saida.append(payload)
    return saida


# --- criterio do teto --------------------------------------------------------


def test_toda_feature_com_eval_tem_teto():
    # A foto do rotulo (nutricao L6) tem eval e snapshot proprios, porque a
    # entrada tem imagem (`test_nutrition_label_eval`,
    # `test_nutrition_label_prompt_snapshot`), e paridade feature x teto em
    # `test_nutrition_label_ai`; as demais (inclusive `meal_parse`, L5b) estao
    # nos casos do IA-11.
    eval_proprio = {AiFeature.LABEL_PHOTO.value}
    assert set(LLM_MAX_OUTPUT_TOKENS) - eval_proprio == set(llm_casos.FEATURES)


@pytest.mark.parametrize("feature", sorted(LLM_MAX_OUTPUT_TOKENS))
def test_teto_tem_folga_ampla_sobre_o_maior_observado(feature):
    teto = LLM_MAX_OUTPUT_TOKENS[feature]
    assert teto >= LLM_OUTPUT_CAP_FLOOR_TOKENS
    assert teto >= LLM_OUTPUT_CAP_HEADROOM_FACTOR * LLM_OUTPUT_OBSERVED_MAX_TOKENS[feature]
    assert teto % openai_client.LLM_OUTPUT_CAP_ROUNDING_TOKENS == 0


def test_feature_sem_observacao_segue_sem_teto_e_sem_chave(monkeypatch):
    enviados = _mock(monkeypatch, {"output_text": "ok"})
    openai_client.call_openai("p", "m", feature="workout_plan")
    # `store=false` vai sempre (IA-17); teto e chave, nao.
    assert enviados == [{"model": "m", "input": "p", "store": False}]


# --- corpo enviado ------------------------------------------------------------


def test_feature_com_prefixo_cacheavel_manda_teto_e_chave(monkeypatch):
    enviados = _mock(monkeypatch, {"output_text": "ok"})
    openai_client.call_openai("p", "m", feature="insights", user_id=42, prompt_version="insights-prompt-v5")
    [corpo] = enviados
    assert corpo["max_output_tokens"] == LLM_MAX_OUTPUT_TOKENS["insights"]
    assert corpo["prompt_cache_key"] == "insights:insights-prompt-v5"
    # Nem usuario nem versao "solta" no corpo: so a chave.
    assert "42" not in corpo["prompt_cache_key"]
    assert "user" not in corpo and "prompt_version" not in corpo


def test_feature_sem_prefixo_cacheavel_manda_so_o_teto(monkeypatch):
    enviados = _mock(monkeypatch, {"output_text": "ok"})
    openai_client.call_openai("p", "m", feature="workout_summary", prompt_version="workout-summary-v1")
    [corpo] = enviados
    assert corpo["max_output_tokens"] == LLM_MAX_OUTPUT_TOKENS["workout_summary"]
    assert "prompt_cache_key" not in corpo


def test_estruturada_tambem_recebe_teto_e_chave(monkeypatch):
    enviados = _mock(monkeypatch, {"output_text": "{}"})
    openai_client.call_openai_structured(
        "p", "m", {}, schema_name="s", feature="periodization", prompt_version="periodization-v10"
    )
    [corpo] = enviados
    assert corpo["max_output_tokens"] == LLM_MAX_OUTPUT_TOKENS["periodization"]
    assert corpo["prompt_cache_key"] == "periodization:periodization-v10"
    assert corpo["text"]["format"]["name"] == "s"


@pytest.mark.parametrize("feature", sorted(PROMPT_CACHE_KEY_FEATURES))
def test_chave_estavel_curta_e_trocada_com_a_versao(feature):
    assert prompt_cache_key(feature, "v1") == prompt_cache_key(feature, "v1")
    assert prompt_cache_key(feature, "v1") != prompt_cache_key(feature, "v2")
    assert len(prompt_cache_key(feature, "x" * 20)) <= 64
    assert prompt_cache_key(feature, None) is None


def test_chave_so_nas_features_do_prefixo_cacheavel():
    assert PROMPT_CACHE_KEY_FEATURES == {"insights", "workouts_analysis", "periodization", "cardio_agent"}
    assert prompt_cache_key("set_evaluation", "set-eval-v2") is None


# --- resposta incompleta --------------------------------------------------------

_INCOMPLETA = {
    "status": "incomplete",
    "incomplete_details": {"reason": "max_output_tokens"},
    "output_text": "TEXTO-CORTADO-NO-MEI",
    "model": "gpt-x",
    "usage": {"input_tokens": 10, "input_tokens_details": {"cached_tokens": 4}, "output_tokens": 16384},
}


def test_incompleta_vira_erro_sem_retry_e_com_log(monkeypatch, caplog):
    enviados = _mock(monkeypatch, _INCOMPLETA)
    with caplog.at_level(logging.INFO, logger="musclefit.events"):
        with pytest.raises(OpenAIGenerationError) as exc:
            openai_client.call_openai(
                "SEGREDO-DO-PROMPT", "m", feature="insights", user_id=7, prompt_version="insights-prompt-v5"
            )
    assert str(exc.value) == openai_client.USER_MESSAGE_INVALID_RESPONSE
    assert "incompleta" in exc.value.diagnostic
    assert len(enviados) == 1  # sem nova tentativa paga
    [uso] = _eventos(caplog, "llm.usage")
    assert uso["status"] == "incomplete" and uso["output_tokens"] == 16384 and uso["cached_tokens"] == 4
    [corte] = _eventos(caplog, "llm.output_incomplete")
    assert corte["reason"] == "max_output_tokens"
    assert corte["max_output_tokens"] == LLM_MAX_OUTPUT_TOKENS["insights"]
    assert corte["feature"] == "insights" and corte["prompt_version"] == "insights-prompt-v5"
    tudo = json.dumps([uso, corte])
    assert "SEGREDO-DO-PROMPT" not in tudo and "TEXTO-CORTADO" not in tudo


def test_incompleta_na_estruturada_nao_devolve_json_parcial(monkeypatch):
    _mock(monkeypatch, {**_INCOMPLETA, "output_text": '{"slots": [{"a": 1}]}'})
    with pytest.raises(OpenAIGenerationError) as exc:
        openai_client.call_openai_structured("p", "m", {}, schema_name="s", feature="periodization")
    assert "incompleta" in exc.value.diagnostic


def test_incompleta_segue_o_caminho_de_falha_do_chamador(monkeypatch):
    """A Analise por IA ja convertia falha da OpenAI em `ReportGenerationError`
    (job `failed` com mensagem); o corte entra no mesmo caminho."""
    _mock(monkeypatch, _INCOMPLETA)
    with pytest.raises(report_ai.ReportGenerationError) as exc:
        report_ai.analyze_training_evolution({}, 30)
    assert str(exc.value) == openai_client.USER_MESSAGE_INVALID_RESPONSE
    assert "incompleta" in exc.value.__cause__.diagnostic


def test_completa_continua_igual(monkeypatch):
    _mock(monkeypatch, {"status": "completed", "output_text": "inteiro"})
    assert openai_client.call_openai("p", "m", feature="insights") == "inteiro"
