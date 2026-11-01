"""Raciocinio por feature no Claude (`ANTHROPIC_THINKING_<FEATURE>`).

- sem a env nova, o corpo enviado as 6 features ja em producao e IDENTICO ao
  de antes (snapshot literal, inclusive a ordem das chaves);
- `disabled` vira `thinking: {"type": "disabled"}` com o esforco da feature;
- `disabled` com esforco `xhigh`/`max` (400 na API) falha antes da rede com
  `ANTHROPIC_CONFIG_INVALID` (elegivel ao fallback);
- valor invalido cai no padrao e registra `llm.config_invalid`."""

from __future__ import annotations

import json
import logging

import pytest

from app import llm_anthropic, openai_client
from app.openai_client import USER_MESSAGE_UNAVAILABLE, OpenAIGenerationError
from tests.unit.test_llm_provider_anthropic import (  # noqa: F401 - _ambiente e fixture autouse
    _ambiente,
    _anthropic_falsa,
    _eventos,
    _mensagem,
    _openai_falsa,
)

# Producao desde 2026-10-31 (infra/llm_providers.tf): feature -> esforco, todas
# no claude-haiku-5-5, sem ANTHROPIC_THINKING_*.
PRODUCAO = {
    "cardio_agent": "medium",
    "label_photo": "medium",
    "insights": "medium",
    "meal_parse": "medium",
    "exercise_description": "medium",
    "workout_summary": "low",
}


def _liga(monkeypatch, feature, effort, model="claude-haiku-5-5"):
    monkeypatch.setenv(llm_anthropic.feature_env("LLM_PROVIDER_", feature), "anthropic")
    monkeypatch.setenv(llm_anthropic.feature_env("ANTHROPIC_MODEL_", feature), model)
    monkeypatch.setenv(llm_anthropic.feature_env("ANTHROPIC_EFFORT_", feature), effort)


@pytest.mark.parametrize(("feature", "effort"), sorted(PRODUCAO.items()))
def test_sem_env_nova_o_corpo_das_features_em_producao_e_o_de_antes(monkeypatch, feature, effort):
    _liga(monkeypatch, feature, effort)
    teto = openai_client.LLM_MAX_OUTPUT_TOKENS[feature]
    esperado = {
        "model": "claude-haiku-5-5",
        "max_tokens": teto,
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": effort},
        "messages": [{"role": "user", "content": [{"type": "text", "text": "P"}]}],
    }
    corpo = llm_anthropic.build_request("P", feature=feature)
    assert json.dumps(corpo) == json.dumps(esperado)
    schema = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"],
              "additionalProperties": False}
    esperado["output_config"] = {"effort": effort, "format": {"type": "json_schema", "schema": schema}}
    assert json.dumps(llm_anthropic.build_request("P", feature=feature, schema=schema)) == json.dumps(esperado)


def test_padrao_e_adaptive_em_toda_feature():
    assert llm_anthropic.DEFAULT_THINKING == llm_anthropic.THINKING_ADAPTIVE
    for feature in llm_anthropic.LLM_FEATURES:
        assert llm_anthropic.anthropic_thinking_for(feature) == "adaptive"


@pytest.mark.parametrize("effort", llm_anthropic.EFFORTS_WITHOUT_THINKING)
def test_disabled_manda_thinking_desligado_com_o_esforco_da_feature(monkeypatch, effort):
    _liga(monkeypatch, "periodization", effort)
    monkeypatch.setenv("ANTHROPIC_THINKING_PERIODIZATION", "disabled")
    cliente = _anthropic_falsa(monkeypatch, _mensagem('{"ok": true}'))
    openai_client.call_openai("p", "gpt-x", timeout=50, feature="periodization")
    [pedido] = cliente.pedidos
    assert pedido["thinking"] == {"type": "disabled"}
    assert pedido["output_config"] == {"effort": effort}
    assert pedido["max_tokens"] == openai_client.LLM_MAX_OUTPUT_TOKENS["periodization"]


def test_disabled_numa_feature_nao_muda_as_outras(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_THINKING_PERIODIZATION", "disabled")
    assert llm_anthropic.anthropic_thinking_for("periodization") == "disabled"
    assert llm_anthropic.anthropic_thinking_for("insights") == "adaptive"


def test_esforcos_sem_raciocinio_sao_subconjunto_dos_esforcos():
    assert set(llm_anthropic.EFFORTS_WITHOUT_THINKING) < set(llm_anthropic.EFFORTS)
    assert set(llm_anthropic.EFFORTS) - set(llm_anthropic.EFFORTS_WITHOUT_THINKING) == {"xhigh", "max"}


@pytest.mark.parametrize("effort", ["xhigh", "max"])
def test_disabled_com_xhigh_ou_max_falha_antes_da_rede(monkeypatch, caplog, effort):
    _liga(monkeypatch, "periodization", effort)
    monkeypatch.setenv("ANTHROPIC_THINKING_PERIODIZATION", "disabled")
    cliente = _anthropic_falsa(monkeypatch)
    monkeypatch.setattr(openai_client, "_post", lambda *a, **k: pytest.fail("sem fallback ligado"))
    with caplog.at_level(logging.INFO, logger="musclefit.events"), pytest.raises(OpenAIGenerationError) as exc:
        openai_client.call_openai("p", "gpt-x", timeout=50, feature="periodization")
    assert cliente.pedidos == []
    assert exc.value.code == llm_anthropic.ANTHROPIC_CONFIG_INVALID
    assert str(exc.value) == USER_MESSAGE_UNAVAILABLE
    assert "ANTHROPIC_EFFORT_PERIODIZATION" in exc.value.diagnostic
    [ev] = _eventos(caplog, "llm.config_invalid")
    assert ev["env"] == "ANTHROPIC_THINKING_PERIODIZATION"


def test_config_invalida_cai_no_fallback_quando_ligado(monkeypatch):
    _liga(monkeypatch, "periodization", "max")
    monkeypatch.setenv("ANTHROPIC_THINKING_PERIODIZATION", "disabled")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER_PERIODIZATION", "openai")
    cliente = _anthropic_falsa(monkeypatch)
    corpos = _openai_falsa(monkeypatch, {"output_text": "da openai", "model": "gpt-5.4-mini"})
    assert openai_client.call_openai("p", "gpt-5.4-mini", timeout=50, feature="periodization") == "da openai"
    assert cliente.pedidos == [] and len(corpos) == 1


def test_valor_invalido_cai_no_adaptive_e_registra(monkeypatch, caplog):
    monkeypatch.setenv("ANTHROPIC_THINKING_INSIGHTS", "desligado")
    with caplog.at_level(logging.INFO, logger="musclefit.events"):
        assert llm_anthropic.anthropic_thinking_for("insights") == "adaptive"
    [ev] = _eventos(caplog, "llm.config_invalid")
    assert ev["env"] == "ANTHROPIC_THINKING_INSIGHTS"
