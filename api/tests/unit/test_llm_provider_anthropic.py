"""Provedor Anthropic por feature no cliente LLM unico (`app.llm_anthropic`).

Rede sempre falsa: o cliente do SDK e trocado por um duble; as excecoes sao as
classes tipadas reais do SDK `anthropic`. Garante, primeiro, que SEM nenhuma
env nova o caminho OpenAI e identico ao de antes (corpo HTTP e eventos)."""

from __future__ import annotations

import json
import logging

import anthropic
import httpx2
import pytest

from app import llm_anthropic, llm_anthropic_schema, openai_client, secrets_bootstrap
from app.domain.nutrition import ai_usage
from app.openai_client import (
    USER_MESSAGE_INVALID_RESPONSE,
    USER_MESSAGE_TIMEOUT,
    USER_MESSAGE_UNAVAILABLE,
    ImageInput,
    OpenAIGenerationError,
)

_ENVS_NOVAS = ("LLM_PROVIDER_", "ANTHROPIC_MODEL_", "ANTHROPIC_EFFORT_", "LLM_FALLBACK_PROVIDER")


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    import os

    for name in list(os.environ):
        if name.startswith(_ENVS_NOVAS):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "chave-openai-de-teste")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-chave-de-teste-123456")
    monkeypatch.setattr(openai_client, "_sleep", lambda s: None)
    llm_anthropic._client_cache.clear()


class _RespostaOpenAI:
    def __init__(self, data):
        self._data = data

    def read(self):
        return json.dumps(self._data).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _openai_falsa(monkeypatch, data=None):
    corpos = []

    def _urlopen(request, timeout=None):
        corpos.append(json.loads(request.data.decode("utf-8")))
        return _RespostaOpenAI(data or {"output_text": '{"ok": true}', "model": "gpt-5.4-mini"})

    monkeypatch.setattr(openai_client.urllib.request, "urlopen", _urlopen)
    return corpos


class _ClienteFalso:
    def __init__(self, respostas):
        self.respostas = list(respostas)
        self.pedidos = []
        self.messages = self

    def create(self, **kwargs):
        self.pedidos.append(kwargs)
        resposta = self.respostas.pop(0)
        if isinstance(resposta, BaseException):
            raise resposta
        return resposta


def _anthropic_falsa(monkeypatch, *respostas):
    cliente = _ClienteFalso(respostas)
    monkeypatch.setattr(llm_anthropic, "_client", lambda api_key: cliente)
    return cliente


def _mensagem(texto="ok", *, stop_reason="end_turn", model="claude-haiku-5-5", usage=None, stop_details=None):
    return {
        "id": "msg_teste",
        "model": model,
        "stop_reason": stop_reason,
        "stop_details": stop_details,
        "content": [
            {"type": "thinking", "thinking": "", "signature": "sig"},
            {"type": "text", "text": texto},
        ],
        "usage": usage
        or {
            "input_tokens": 100,
            "cache_read_input_tokens": 40,
            "cache_creation_input_tokens": 10,
            "output_tokens": 30,
            "output_tokens_details": {"thinking_tokens": 12},
        },
    }


def _erro_http(cls, status, tipo, mensagem):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    body = {"type": "error", "error": {"type": tipo, "message": mensagem}}
    response = httpx2.Response(status, request=request, json=body, headers={"request-id": "req_teste"})
    return cls(mensagem, response=response, body=body)


def _eventos(caplog, nome):
    saida = []
    for rec in caplog.records:
        if rec.name == "musclefit.events":
            payload = json.loads(rec.getMessage())
            if payload.get("event") == nome:
                saida.append(payload)
    return saida


# --- Padroes: nada muda sem env nova ------------------------------------------


@pytest.mark.parametrize("feature", llm_anthropic.LLM_FEATURES)
def test_sem_env_toda_feature_fica_na_openai(feature):
    assert llm_anthropic.provider_for(feature) == "openai"
    assert llm_anthropic.fallback_provider_for(feature) is None


def test_sem_env_o_corpo_http_da_openai_e_o_de_antes(monkeypatch, caplog):
    corpos = _openai_falsa(monkeypatch)
    monkeypatch.setattr(llm_anthropic, "_client", lambda api_key: pytest.fail("Anthropic nao pode ser chamada"))
    with caplog.at_level(logging.INFO, logger="musclefit.events"):
        openai_client.call_openai("p", "gpt-x", feature="insights", prompt_version="v1", cache_prefix="p")
        openai_client.call_openai_structured("p", "gpt-x", {"type": "object"}, schema_name="s", feature="periodization")
    assert corpos[0] == openai_client._request_body(
        {"model": "gpt-x", "input": "p"}, feature="insights", prompt_version="v1"
    )
    assert "cache_prefix" not in json.dumps(corpos)
    assert corpos[1]["text"]["format"]["schema"] == {"type": "object"}
    assert {ev["provider"] for ev in _eventos(caplog, "llm.usage")} == {"openai"}


def test_env_de_outra_feature_nao_muda_esta(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_SET_EVALUATION", "anthropic")
    corpos = _openai_falsa(monkeypatch)
    openai_client.call_openai("p", "gpt-x", feature="insights")
    assert corpos and corpos[0]["model"] == "gpt-x"


def test_valor_invalido_cai_na_openai_e_registra(monkeypatch, caplog):
    monkeypatch.setenv("LLM_PROVIDER_INSIGHTS", "gemini")
    with caplog.at_level(logging.INFO, logger="musclefit.events"):
        assert llm_anthropic.provider_for("insights") == "openai"
    assert _eventos(caplog, "llm.config_invalid")[0]["env"] == "LLM_PROVIDER_INSIGHTS"


def test_padroes_de_modelo_e_esforco_por_feature():
    for feature in ("set_evaluation", "exercise_summary", "workout_summary"):
        assert llm_anthropic.anthropic_model_for(feature) == "claude-haiku-5-5"
        assert llm_anthropic.anthropic_effort_for(feature) == "low"
    for feature in ("periodization", "cardio_agent", "insights", "workouts_analysis", "label_photo",
                    "meal_parse", "exercise_description", "workout_plan"):
        assert llm_anthropic.anthropic_model_for(feature) == "claude-sonnet-5-5"
        assert llm_anthropic.anthropic_effort_for(feature) == "medium"


def test_envs_de_modelo_e_esforco_vencem_o_padrao(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL_MEAL_PARSE", "claude-haiku-5-5")
    monkeypatch.setenv("ANTHROPIC_EFFORT_MEAL_PARSE", "high")
    assert llm_anthropic.anthropic_model_for("meal_parse") == "claude-haiku-5-5"
    assert llm_anthropic.anthropic_effort_for("meal_parse") == "high"


# --- Pedido -------------------------------------------------------------------


def test_texto_na_anthropic_pedido_e_resposta(monkeypatch, caplog):
    monkeypatch.setenv("LLM_PROVIDER_SET_EVALUATION", "anthropic")
    cliente = _anthropic_falsa(monkeypatch, _mensagem("resposta final"))
    monkeypatch.setattr(openai_client, "_post", lambda *a, **k: pytest.fail("OpenAI nao pode ser chamada"))
    with caplog.at_level(logging.INFO, logger="musclefit.events"):
        texto = openai_client.call_openai("PROMPT-SECRETO", "gpt-x", timeout=50, feature="set_evaluation", user_id=7)
    assert texto == "resposta final"  # so blocos de texto, o thinking fica de fora
    [pedido] = cliente.pedidos
    assert pedido["model"] == "claude-haiku-5-5"
    assert pedido["max_tokens"] == openai_client.LLM_MAX_OUTPUT_TOKENS["set_evaluation"]
    assert pedido["thinking"] == {"type": "adaptive"}
    assert pedido["output_config"] == {"effort": "low"}
    assert pedido["timeout"] == 50
    assert pedido["messages"] == [{"role": "user", "content": [{"type": "text", "text": "PROMPT-SECRETO"}]}]
    for proibido in ("temperature", "top_p", "top_k", "system", "store"):
        assert proibido not in pedido
    [ev] = _eventos(caplog, "llm.usage")
    assert ev["provider"] == "anthropic" and ev["status"] == "ok" and ev["model"] == "claude-haiku-5-5"
    assert (ev["input_tokens"], ev["cached_tokens"], ev["cache_write_tokens"]) == (150, 40, 10)
    assert (ev["output_tokens"], ev["reasoning_tokens"]) == (30, 12)
    assert "PROMPT-SECRETO" not in json.dumps(ev) and "resposta final" not in json.dumps(ev)


def test_estruturado_usa_output_config_e_devolve_a_forma_original(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_CARDIO_AGENT", "anthropic")
    schema = {
        "type": "object",
        "properties": {"a": {"type": "string"}, "b": {"type": ["integer", "null"]}},
        "required": ["a", "b"],
        "additionalProperties": False,
    }
    cliente = _anthropic_falsa(monkeypatch, _mensagem('{"a": "x", "b": null}', model="claude-sonnet-5-5"))
    result = openai_client.call_openai_structured("p", "gpt-x", schema, schema_name="s", feature="cardio_agent")
    assert result.data == {"a": "x", "b": None}
    assert result.model == "claude-sonnet-5-5" and (result.tokens_in, result.tokens_out) == (150, 30)
    formato = cliente.pedidos[0]["output_config"]
    assert formato == {"effort": "medium", "format": {"type": "json_schema", "schema": schema}}


def test_visao_vai_em_bloco_image_base64(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_LABEL_PHOTO", "anthropic")
    cliente = _anthropic_falsa(monkeypatch, _mensagem('{"x": 1}'))
    imagem = ImageInput(data=b"\xff\xd8bytes", content_type="image/jpeg", detail="high")
    openai_client.call_openai_structured_vision(
        "leia", [imagem], "gpt-x", {"type": "object"}, schema_name="s", feature="label_photo"
    )
    [bloco_img, bloco_txt] = cliente.pedidos[0]["messages"][0]["content"]
    assert bloco_img == {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "/9hieXRlcw=="}}
    assert bloco_txt == {"type": "text", "text": "leia"}


def test_cache_control_no_prefixo_estavel_so_nas_features_com_cache_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_INSIGHTS", "anthropic")
    monkeypatch.setenv("LLM_PROVIDER_EXERCISE_SUMMARY", "anthropic")
    cliente = _anthropic_falsa(monkeypatch, _mensagem(), _mensagem(), _mensagem())
    openai_client.call_openai("REGRAS+RAG|dados", "m", feature="insights", prompt_version="v1", cache_prefix="REGRAS+RAG")
    openai_client.call_openai("REGRAS+RAG|dados", "m", feature="insights", prompt_version="v1", cache_prefix="OUTRO")
    openai_client.call_openai("REGRAS|dados", "m", feature="exercise_summary", prompt_version="v1", cache_prefix="REGRAS")
    com_cache, prefixo_errado, sem_cache_key = (p["messages"][0]["content"] for p in cliente.pedidos)
    assert com_cache == [
        {"type": "text", "text": "REGRAS+RAG", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "|dados"},
    ]
    assert prefixo_errado == [{"type": "text", "text": "REGRAS+RAG|dados"}]
    assert sem_cache_key == [{"type": "text", "text": "REGRAS|dados"}]


# --- Retry, recusa, corte e erros ---------------------------------------------


def test_retry_em_529_e_depois_sucesso(monkeypatch, caplog):
    monkeypatch.setenv("LLM_PROVIDER_INSIGHTS", "anthropic")
    sobrecarga = _erro_http(anthropic.OverloadedError, 529, "overloaded_error", "Overloaded")
    cliente = _anthropic_falsa(monkeypatch, sobrecarga, _mensagem("ok"))
    with caplog.at_level(logging.INFO, logger="musclefit.events"):
        assert openai_client.call_openai("p", "m", timeout=50, feature="insights") == "ok"
    assert len(cliente.pedidos) == 2
    [ev] = _eventos(caplog, "llm.usage")
    assert ev["attempts"] == 2 and ev["status"] == "ok"
    [erro] = _eventos(caplog, "llm.error")
    assert erro["will_retry"] is True and erro["status"] == 529 and erro["provider"] == "anthropic"


def test_timeout_nao_repete_nem_faz_fallback(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_INSIGHTS", "anthropic")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "openai")
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    cliente = _anthropic_falsa(monkeypatch, anthropic.APITimeoutError(request=request))
    monkeypatch.setattr(openai_client, "_post", lambda *a, **k: pytest.fail("sem fallback em timeout"))
    with pytest.raises(OpenAIGenerationError) as exc:
        openai_client.call_openai("p", "m", feature="insights")
    assert str(exc.value) == USER_MESSAGE_TIMEOUT and exc.value.code == "OPENAI_TIMEOUT"
    assert len(cliente.pedidos) == 1


def test_recusa_vira_erro_tratado_com_mensagem_generica(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_INSIGHTS", "anthropic")
    recusa = _mensagem("", stop_reason="refusal", stop_details={"type": "refusal", "category": "bio", "explanation": "x"})
    _anthropic_falsa(monkeypatch, recusa)
    with pytest.raises(OpenAIGenerationError) as exc:
        openai_client.call_openai("p", "m", feature="insights")
    assert exc.value.code == llm_anthropic.ANTHROPIC_REFUSAL
    assert str(exc.value) == USER_MESSAGE_INVALID_RESPONSE
    assert "bio" in exc.value.diagnostic


def test_max_tokens_vira_saida_incompleta_sem_fallback(monkeypatch, caplog):
    monkeypatch.setenv("LLM_PROVIDER_PERIODIZATION", "anthropic")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER_PERIODIZATION", "openai")
    _anthropic_falsa(monkeypatch, _mensagem('{"cortado', stop_reason="max_tokens"))
    monkeypatch.setattr(openai_client, "_post", lambda *a, **k: pytest.fail("saida cortada nao vai para a OpenAI"))
    with caplog.at_level(logging.INFO, logger="musclefit.events"):
        with pytest.raises(OpenAIGenerationError) as exc:
            openai_client.call_openai_structured("p", "m", {"type": "object"}, schema_name="s", feature="periodization")
    assert str(exc.value) == USER_MESSAGE_INVALID_RESPONSE
    [ev] = _eventos(caplog, "llm.output_incomplete")
    assert ev["reason"] == "max_tokens" and ev["provider"] == "anthropic"
    assert ev["max_output_tokens"] == openai_client.LLM_MAX_OUTPUT_TOKENS["periodization"]
    assert _eventos(caplog, "llm.usage")[0]["status"] == "incomplete"


@pytest.mark.parametrize(
    ("erro", "codigo"),
    [
        (_erro_http(anthropic.BadRequestError, 400, "invalid_request_error",
                    "Your credit balance is too low to access the Anthropic API."), "ANTHROPIC_BILLING"),
        (_erro_http(anthropic.APIStatusError, 402, "billing_error", "Billing problem"), "ANTHROPIC_BILLING"),
        (_erro_http(anthropic.AuthenticationError, 401, "authentication_error",
                    "invalid x-api-key sk-ant-api03-vazou-aqui-123"), "ANTHROPIC_AUTH"),
    ],
)
def test_billing_e_auth_viram_codigo_proprio_e_mascaram_a_chave(monkeypatch, caplog, erro, codigo):
    monkeypatch.setenv("LLM_PROVIDER_INSIGHTS", "anthropic")
    _anthropic_falsa(monkeypatch, erro)
    with caplog.at_level(logging.INFO, logger="musclefit.events"):
        with pytest.raises(OpenAIGenerationError) as exc:
            openai_client.call_openai("p", "m", feature="insights")
    assert exc.value.code == codigo and str(exc.value) == USER_MESSAGE_UNAVAILABLE
    assert exc.value.request_id == "req_teste"
    registro = json.dumps(_eventos(caplog, "llm.error"))
    assert "sk-ant-api03-vazou" not in registro and "sk-ant-api03-chave" not in registro


def test_sem_chave_anthropic_responde_nao_configurada(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_INSIGHTS", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    with pytest.raises(OpenAIGenerationError) as exc:
        openai_client.call_openai("p", "m", feature="insights")
    assert exc.value.code == llm_anthropic.ANTHROPIC_NOT_CONFIGURED and str(exc.value) == USER_MESSAGE_UNAVAILABLE


# --- Fallback -----------------------------------------------------------------


@pytest.mark.parametrize(
    "falha",
    [
        _erro_http(anthropic.BadRequestError, 400, "invalid_request_error", "Your credit balance is too low."),
        _erro_http(anthropic.InternalServerError, 500, "api_error", "boom"),
        _mensagem("", stop_reason="refusal"),
    ],
)
def test_fallback_para_a_openai_com_o_modelo_openai_da_feature(monkeypatch, caplog, falha):
    monkeypatch.setenv("LLM_PROVIDER_INSIGHTS", "anthropic")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "openai")
    # 500 repete ate a janela; sempre falha.
    _anthropic_falsa(monkeypatch, *([falha] * 3))
    corpos = _openai_falsa(monkeypatch, {"output_text": "da openai", "model": "gpt-5.4-mini"})
    with caplog.at_level(logging.INFO, logger="musclefit.events"):
        texto = openai_client.call_openai("p", "gpt-5.4-mini", timeout=50, feature="insights", prompt_version="v1")
    assert texto == "da openai"
    assert corpos[0]["model"] == "gpt-5.4-mini"
    [ev] = _eventos(caplog, "llm.fallback")
    assert (ev["from_provider"], ev["to_provider"], ev["feature"]) == ("anthropic", "openai", "insights")


def test_sem_fallback_ligado_o_erro_sobe(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_INSIGHTS", "anthropic")
    _anthropic_falsa(monkeypatch, _mensagem("", stop_reason="refusal"))
    monkeypatch.setattr(openai_client, "_post", lambda *a, **k: pytest.fail("fallback desligado"))
    with pytest.raises(OpenAIGenerationError):
        openai_client.call_openai("p", "m", feature="insights")


def test_env_de_fallback_da_feature_vence_a_global(monkeypatch):
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER", "openai")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER_INSIGHTS", "nenhum")
    assert llm_anthropic.fallback_provider_for("insights") is None
    assert llm_anthropic.fallback_provider_for("cardio_agent") == "openai"


# --- Chave e preco ------------------------------------------------------------


def test_secrets_bootstrap_carrega_a_chave_da_anthropic():
    assert ("ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY_SECRET_ARN") in secrets_bootstrap._SECRETS


def test_preco_do_claude_pelo_dono_com_a_faixa_do_haiku():
    assert ai_usage.price_for_model("claude-sonnet-5-5") == ai_usage.MODEL_PRICES_USD_CENTS_PER_MTOKEN["claude-sonnet-5-5"]
    limiar, longo = ai_usage.MODEL_LONG_PROMPT_PRICES["claude-haiku-5-5"]
    assert ai_usage.price_for_model("claude-haiku-5-5", limiar) == ai_usage.MODEL_PRICES_USD_CENTS_PER_MTOKEN["claude-haiku-5-5"]
    assert ai_usage.price_for_model("claude-haiku-5-5", limiar + 1) == longo
    assert ai_usage.price_for_model("claude-haiku-5-5-20261001") is not None  # snapshot datado
    # O `model` que o cliente devolve casa com a tabela.
    for modelo in set(llm_anthropic.DEFAULT_ANTHROPIC_MODEL.values()):
        assert ai_usage.price_for_model(modelo) is not None
    assert ai_usage.estimate_cost_usd("claude-haiku-5-5", limiar + 1, 0) > ai_usage.estimate_cost_usd(
        "claude-haiku-5-5", limiar, 0
    ) * 4


def test_adaptador_de_schema_tem_limites_com_fonte():
    assert llm_anthropic_schema.SCHEMA_LIMITS_SOURCE_URL.startswith("https://platform.claude.com/")
