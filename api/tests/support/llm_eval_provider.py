"""Escolha do provedor/modelo no eval PAGO (`tests/eval/`), para comparar
GPT x Claude feature a feature com os MESMOS casos e criterios.

    # OpenAI (padrao, como sempre):
    OPENAI_API_KEY=... python -m pytest -s -m llm_eval tests/eval/test_llm_features_eval.py
    # Claude em todas as features do eval (modelo padrao de cada feature):
    LLM_EVAL_PROVIDER=anthropic ANTHROPIC_API_KEY=... python -m pytest -s -m llm_eval tests/eval
    # Claude com outro modelo/esforco:
    LLM_EVAL_PROVIDER=anthropic LLM_EVAL_MODEL=claude-sonnet-5-5 LLM_EVAL_EFFORT=high ...
    # Sem raciocinio (so com esforco low/medium/high):
    LLM_EVAL_PROVIDER=anthropic LLM_EVAL_MODEL=claude-haiku-5-5 LLM_EVAL_EFFORT=high LLM_EVAL_THINKING=disabled ...

`LLM_EVAL_PROVIDER` / `LLM_EVAL_MODEL` / `LLM_EVAL_EFFORT` / `LLM_EVAL_THINKING`
so viram as envs de producao da feature (`LLM_PROVIDER_<FEATURE>`,
`ANTHROPIC_MODEL_<FEATURE>`, `ANTHROPIC_EFFORT_<FEATURE>`,
`ANTHROPIC_THINKING_<FEATURE>`, lidas por `app.llm_anthropic`) DURANTE o teste
(monkeypatch): o eval passa pelo mesmo cliente unico que a producao usa. Sem
a chave do provedor escolhido, o caso da SKIP (nunca falha por falta de
chave). Nada aqui chama a rede.
"""

from __future__ import annotations

import os

import pytest

from app import llm_anthropic

ENV_EVAL_PROVIDER = "LLM_EVAL_PROVIDER"
ENV_EVAL_MODEL = "LLM_EVAL_MODEL"
ENV_EVAL_EFFORT = "LLM_EVAL_EFFORT"
ENV_EVAL_THINKING = "LLM_EVAL_THINKING"

_KEY_ENV = {
    llm_anthropic.PROVIDER_OPENAI: "OPENAI_API_KEY",
    llm_anthropic.PROVIDER_ANTHROPIC: llm_anthropic.ENV_API_KEY,
}


def eval_provider() -> str:
    value = (os.environ.get(ENV_EVAL_PROVIDER) or "").strip().lower() or llm_anthropic.DEFAULT_PROVIDER
    if value not in llm_anthropic.PROVIDERS:
        raise ValueError(f"{ENV_EVAL_PROVIDER}={value!r} (use {', '.join(llm_anthropic.PROVIDERS)})")
    return value


def key_env(provider: str) -> str:
    return _KEY_ENV[provider]


def apply(monkeypatch, feature: str) -> str:
    """Liga a `feature` no provedor do eval (e no modelo/esforco pedidos) e
    da SKIP sem a chave dele. Devolve o provedor."""
    provider = eval_provider()
    if not os.environ.get(key_env(provider)):
        pytest.skip(f"{key_env(provider)} não configurada: o eval pago precisa da chave real.")
    monkeypatch.setenv(llm_anthropic.feature_env(llm_anthropic.ENV_PROVIDER_PREFIX, feature), provider)
    if provider == llm_anthropic.PROVIDER_ANTHROPIC:
        model = (os.environ.get(ENV_EVAL_MODEL) or "").strip()
        effort = (os.environ.get(ENV_EVAL_EFFORT) or "").strip()
        if model:
            monkeypatch.setenv(llm_anthropic.feature_env(llm_anthropic.ENV_MODEL_PREFIX, feature), model)
        if effort:
            monkeypatch.setenv(llm_anthropic.feature_env(llm_anthropic.ENV_EFFORT_PREFIX, feature), effort)
        thinking = (os.environ.get(ENV_EVAL_THINKING) or "").strip()
        if thinking:
            monkeypatch.setenv(llm_anthropic.feature_env(llm_anthropic.ENV_THINKING_PREFIX, feature), thinking)
    return provider


__all__ = [
    "ENV_EVAL_EFFORT", "ENV_EVAL_MODEL", "ENV_EVAL_PROVIDER", "ENV_EVAL_THINKING", "apply", "eval_provider", "key_env",
]
