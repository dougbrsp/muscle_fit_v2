"""Virada de 6 features para o Claude Haiku 5.5 (rede falsa, nada pago):

- a feature grava o modelo que RESPONDEU (Claude, ou a OpenAI depois do
  fallback), nao o nome OpenAI configurado (`TextResult`/`StructuredResult`);
- a impressao digital do resumo de treino usa o nome CONFIGURADO do modelo que
  respondeu, para o dedupe bater na proxima chamada igual;
- o custo da escrita no cache de 5 min da Anthropic vem do dono
  (`ai_usage.ANTHROPIC_CACHE_WRITE_5M_MULTIPLIER`).
"""

from __future__ import annotations

from decimal import Decimal

import anthropic

from app import llm_anthropic, openai_client, workout_summary_ai
from app.domain.nutrition import ai_usage
from tests.unit.test_llm_provider_anthropic import (  # noqa: F401 - _ambiente e fixture autouse
    _ambiente,
    _anthropic_falsa,
    _erro_http,
    _mensagem,
    _openai_falsa,
)


# --- Modelo realmente usado ---------------------------------------------------


def test_texto_na_openai_devolve_o_eco_e_o_pedido(monkeypatch):
    _openai_falsa(monkeypatch, {"output_text": "oi", "model": "gpt-5.4-mini-2026-03-17"})
    r = openai_client.call_openai_text("p", "gpt-5.4-mini", feature="workout_summary")
    assert (r.text, r.model, r.requested_model) == ("oi", "gpt-5.4-mini-2026-03-17", "gpt-5.4-mini")


def test_texto_no_claude_devolve_o_modelo_do_claude(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_WORKOUT_SUMMARY", "anthropic")
    monkeypatch.setenv("ANTHROPIC_MODEL_WORKOUT_SUMMARY", "claude-haiku-5-5")
    _anthropic_falsa(monkeypatch, _mensagem("resumo", model="claude-haiku-5-5"))
    r = openai_client.call_openai_text("p", "gpt-5.4-mini", feature="workout_summary")
    assert (r.text, r.model, r.requested_model) == ("resumo", "claude-haiku-5-5", "claude-haiku-5-5")


def test_texto_com_fallback_devolve_o_modelo_da_openai(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_INSIGHTS", "anthropic")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER_INSIGHTS", "openai")
    falha = _erro_http(anthropic.BadRequestError, 400, "invalid_request_error", "Your credit balance is too low.")
    _anthropic_falsa(monkeypatch, falha)
    _openai_falsa(monkeypatch, {"output_text": "da openai", "model": "gpt-5.4-mini"})
    r = openai_client.call_openai_text("p", "gpt-5.4-mini", timeout=50, feature="insights")
    assert (r.text, r.model, r.requested_model) == ("da openai", "gpt-5.4-mini", "gpt-5.4-mini")


def test_insights_grava_o_modelo_que_respondeu(monkeypatch):
    from app import insights_ai

    monkeypatch.setenv("LLM_PROVIDER_INSIGHTS", "anthropic")
    monkeypatch.setattr(insights_ai, "build_insight_prompt", lambda *a, **k: "p")
    monkeypatch.setattr(insights_ai, "insight_cache_prefix", lambda *a, **k: None)
    _anthropic_falsa(monkeypatch, _mensagem("insight", model="claude-haiku-5-5"))
    out = insights_ai.generate_insight("q", {}, "")
    assert (out["model"], out["insight"]) == ("claude-haiku-5-5", "insight")


def test_cardio_grava_o_modelo_que_respondeu(monkeypatch):
    from app import cardio_agent_ai

    resultado = openai_client.StructuredResult(data={}, model="claude-haiku-5-5", tokens_in=1, tokens_out=1)
    monkeypatch.setattr(cardio_agent_ai, "_call_openai_structured", lambda *a, **k: resultado)
    assert cardio_agent_ai.call_openai("p").model == "claude-haiku-5-5"


# --- Impressao digital do resumo de treino (dedupe IA-14) ---------------------


def test_impressao_na_openai_e_a_de_antes():
    """Sem a env, a impressao e a de sempre (modelo OpenAI configurado): os
    resumos gravados antes continuam deduplicando."""
    assert workout_summary_ai.input_fingerprint("p") == workout_summary_ai.input_fingerprint(
        "p", workout_summary_ai.OPENAI_MODEL
    )


def test_impressao_segue_o_provedor_primario_e_a_gravada_bate_com_a_proxima(monkeypatch):
    antes = workout_summary_ai.input_fingerprint("p")
    monkeypatch.setenv("LLM_PROVIDER_WORKOUT_SUMMARY", "anthropic")
    monkeypatch.setenv("ANTHROPIC_MODEL_WORKOUT_SUMMARY", "claude-haiku-5-5")
    depois = workout_summary_ai.input_fingerprint("p")
    # Trocar de provedor muda a impressao (regera so sob demanda).
    assert depois != antes
    assert depois == workout_summary_ai.input_fingerprint("p", "claude-haiku-5-5")

    monkeypatch.setattr(workout_summary_ai, "build_prompt", lambda *a, **k: "p")
    _anthropic_falsa(monkeypatch, _mensagem("resumo", model="claude-haiku-5-5-20261001"))
    out = workout_summary_ai.generate_workout_summary({"id": 1})
    assert out["model"] == "claude-haiku-5-5-20261001"  # eco gravado como veio
    assert out["input_sha256"] == depois  # chave pelo nome configurado: o dedupe bate


def test_impressao_depois_do_fallback_usa_o_modelo_openai(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_WORKOUT_SUMMARY", "anthropic")
    monkeypatch.setenv("LLM_FALLBACK_PROVIDER_WORKOUT_SUMMARY", "openai")
    monkeypatch.setattr(workout_summary_ai, "build_prompt", lambda *a, **k: "p")
    falha = _erro_http(anthropic.AuthenticationError, 401, "authentication_error", "invalid x-api-key")
    _anthropic_falsa(monkeypatch, falha)
    _openai_falsa(monkeypatch, {"output_text": "da openai", "model": "gpt-5.4-mini-2026-03-17"})
    out = workout_summary_ai.generate_workout_summary({"id": 1})
    assert out["model"] == "gpt-5.4-mini-2026-03-17"
    assert out["input_sha256"] == workout_summary_ai.input_fingerprint("p", workout_summary_ai.OPENAI_MODEL)


# --- Custo da escrita no cache (dono: ai_usage) -------------------------------


def test_escrita_no_cache_custa_1_25x_a_entrada_no_claude():
    assert ai_usage.ANTHROPIC_CACHE_WRITE_5M_MULTIPLIER == Decimal("1.25")
    for modelo in ("claude-haiku-5-5", "claude-sonnet-5-5"):
        preco = ai_usage.price_for_model(modelo)
        assert preco.cache_write_cents == preco.input_cents * ai_usage.ANTHROPIC_CACHE_WRITE_5M_MULTIPLIER
    _, longo = ai_usage.MODEL_LONG_PROMPT_PRICES["claude-haiku-5-5"]
    assert longo.cache_write_cents == longo.input_cents * ai_usage.ANTHROPIC_CACHE_WRITE_5M_MULTIPLIER
    # Pagina oficial (2026-10-31): Haiku 5.5 US$ 0,125/MTok; Sonnet 5.5 US$ 2,50/MTok.
    assert ai_usage.price_for_model("claude-haiku-5-5").cache_write_cents == Decimal("12.5")
    assert ai_usage.price_for_model("claude-sonnet-5-5").cache_write_cents == Decimal(250)


def test_custo_com_escrita_no_cache():
    # Sonnet: 1M de entrada, sendo 400k escritos e 100k lidos do cache; 0 de saida.
    # 500k x 200 + 400k x 250 + 100k x 10 = 201.000.000 centavos-token -> US$ 2,01.
    assert ai_usage.estimate_cost_usd("claude-sonnet-5-5", 1_000_000, 0, 100_000, 400_000) == Decimal("2.010000")
    # Sem a contagem de escrita: preco cheio da entrada (a conta de antes).
    assert ai_usage.estimate_cost_usd("claude-sonnet-5-5", 1_000_000, 0, 100_000) == Decimal("1.810000")
    # Escrita nunca passa do que sobra da entrada.
    assert ai_usage.estimate_cost_usd("claude-sonnet-5-5", 100, 0, 60, 1_000) == ai_usage.estimate_cost_usd(
        "claude-sonnet-5-5", 100, 0, 60, 40
    )


def test_openai_nao_cobra_escrita_no_cache_a_parte():
    assert ai_usage.price_for_model("gpt-5.4-mini").cache_write_cents is None
    assert ai_usage.estimate_cost_usd("gpt-5.4-mini", 2000, 500, 0, 1000) == ai_usage.estimate_cost_usd(
        "gpt-5.4-mini", 2000, 500
    )
    assert "cache_write_cents" not in ai_usage.price_for_model("gpt-5.4-mini").to_dict()


def test_leitura_de_cache_do_sonnet_5_5_e_0_05x_da_entrada():
    """Pagina oficial: 'Cache hits and refreshes on Claude Opus 5.5 and Claude
    Sonnet 5.5 are priced at 0.05x the base input price' (US$ 0,10/MTok)."""
    preco = ai_usage.price_for_model("claude-sonnet-5-5")
    assert preco.cached_input_cents == preco.input_cents * Decimal("0.05") == Decimal(10)
    assert ai_usage.ANTHROPIC_PRICES_CHECKED_ON == "2026-10-31"


def test_haiku_e_o_modelo_das_features_viradas_tem_preco():
    assert ai_usage.price_for_model(llm_anthropic.ANTHROPIC_MODEL_HAIKU) is not None
