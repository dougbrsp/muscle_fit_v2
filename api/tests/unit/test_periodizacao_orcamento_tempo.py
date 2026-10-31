"""Orcamento de tempo da Periodizacao (migracao Claude, 2026-10-31).

- a chamada a LLM cabe no timeout da Lambda com a folga reservada, derivada
  da fonte unica (`async_jobs.LAMBDA_TIMEOUT_SECONDS`, paridade com o Terraform);
- `run_periodization_draft` passa esse teto e a prioridade do adaptador
  (`ANTHROPIC_OPTIONAL_FIRST`) ao cliente unico;
- o espelho do frontend (`periodization_job`) e o prazo do job no poll."""

from __future__ import annotations

import contextlib

from app import async_jobs
from app.openai_client import StructuredResult
from app.periodization import core
from app.periodization.output_schema import ANTHROPIC_OPTIONAL_FIRST


def test_teto_da_chamada_e_o_timeout_da_lambda_menos_a_folga():
    lambda_s = async_jobs.LAMBDA_TIMEOUT_SECONDS[core.PLAN_GENERATOR_FUNCTION]
    assert core.LLM_TIMEOUT_SECONDS == lambda_s - core.LAMBDA_RESERVED_SECONDS
    assert 0 < core.LLM_TIMEOUT_SECONDS < lambda_s
    assert core.OPENAI_TIMEOUT_SECONDS == core.LLM_TIMEOUT_SECONDS  # nome antigo
    assert async_jobs.JOB_TYPE_LAMBDA[core.JOB_TYPE] == core.PLAN_GENERATOR_FUNCTION


def test_contexto_lento_encolhe_a_chamada_para_caber_na_lambda():
    lambda_s = async_jobs.LAMBDA_TIMEOUT_SECONDS[core.PLAN_GENERATOR_FUNCTION]
    assert core.llm_timeout_for(0) == core.LLM_TIMEOUT_SECONDS  # contexto rapido: teto cheio
    lento = core.LAMBDA_RESERVED_SECONDS + 10
    assert core.llm_timeout_for(lento) == lambda_s - core.POST_LLM_RESERVED_SECONDS - lento
    assert lento + core.llm_timeout_for(lento) + core.POST_LLM_RESERVED_SECONDS == lambda_s
    assert 0 < core.POST_LLM_RESERVED_SECONDS < core.LAMBDA_RESERVED_SECONDS


def test_espelho_do_frontend_e_o_prazo_do_job():
    exportado = core.export_for_frontend()
    assert exportado == {
        "llm_timeout_seconds": core.LLM_TIMEOUT_SECONDS,
        "job_deadline_seconds": async_jobs.worker_lost_deadline_seconds(core.JOB_TYPE),
    }
    assert exportado["job_deadline_seconds"] > exportado["llm_timeout_seconds"]


def test_run_passa_teto_e_prioridade_do_adaptador(monkeypatch):
    chamadas: list[dict] = []

    def _fake(prompt, model, schema, **kwargs):
        chamadas.append(kwargs)
        return StructuredResult(data={"template": {}}, model=model, tokens_in=1, tokens_out=1)

    monkeypatch.setenv(core.MODEL_ENV_VAR, "gpt-x")
    monkeypatch.setattr(core, "call_openai_structured", _fake)
    monkeypatch.setattr(core, "transaction", lambda: contextlib.nullcontext(None))
    monkeypatch.setattr(core, "build_context", lambda conn, request, user_id: {})
    monkeypatch.setattr(core, "build_prompt", lambda context: "prompt")
    monkeypatch.setattr(core, "build_prompt_cache_prefix", lambda context: None)
    monkeypatch.setattr(core, "finish_log", lambda *a, **k: None)
    monkeypatch.setattr(
        core,
        "validate_draft",
        lambda conn, request, data, **_kw: (data, {"errors": [], "warnings": []}, {"rationale": "", "source_citations": []}),
    )
    core.run_periodization_draft({}, user_id=None)
    assert chamadas[0]["timeout"] == core.LLM_TIMEOUT_SECONDS
    assert chamadas[0]["anthropic_optional_first"] == ANTHROPIC_OPTIONAL_FIRST
    assert chamadas[0]["feature"] == "periodization"
