"""O job da periodizacao: contexto -> LLM com saida estruturada -> validacao
deterministica -> rascunho (PLANO_20260912.md secao 7.1).

Roda na Lambda `musclefit-plan-generator` (`lambda_plan_generator.py` despacha
por `job_kind`), reaproveitando imagem, timeout (180 s) e IAM que ja existem.

**Nada aqui escreve em `workout_plan_templates`** (nem em slots/itens). As
UNICAS escritas deste modulo sao em `template_generation_logs` (auditoria). O
modelo de treino so passa a existir quando o usuario, depois de editar o
rascunho, chama o `POST /workout-plan-templates` de sempre.

Sem retry: uma chamada de LLM por job. Retry dobraria o custo e nao cabe no
timeout da Lambda (`LLM_TIMEOUT_SECONDS` da chamada + contexto + validacao).
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Optional

from ..async_jobs import LAMBDA_TIMEOUT_SECONDS
from ..db import transaction
from ..models import PeriodizationDraftIn
from ..observability import EVENT_PERIODIZATION_VALIDATION_FAILED, log_event
from ..openai_client import (
    TEXT_RETRY_MIN_ATTEMPT_SECONDS,
    USER_MESSAGE_TIMEOUT,
    USER_MESSAGE_UNAVAILABLE,
    OpenAIGenerationError,
    call_openai_structured,
)
from . import catalog_refs
from .context import build_context, build_prompt, build_prompt_cache_prefix
from .logs import STATUS_FAILED, STATUS_SUCCEEDED, finish_log
from .output_schema import ANTHROPIC_OPTIONAL_FIRST, SCHEMA_NAME, output_schema
from .validation import validate_draft

logger = logging.getLogger("musclefit.periodization")

# v5 (Fase 6, PR 2): a tabela da metodologia vem do cadastro de macro-grupos
# (057) - "macro-grupo <nome>" no lugar do porte `size_class` - e traz a meta
# SEMANAL de séries diretas; o validador avisa também no eixo semanal.
# v6 (Fase 7, Frente B): o item ganha `rest_seconds` (descanso por exercício do
# Modelo); o prompt manda deixar null e o validador descarta qualquer valor.
# v7 (Fase 5, PR 5): as series do item somam o equivalente PLANEJADO da tecnica
# (EXG-014), FST-7/GVT vem com aplicacoes/pausa null (auto-fix no validador) e
# `techniques` traz os coeficientes do catalogo.
# v8 (M76, Lote 4c - E10): todo numero do prompt interpolado da fonte (faixa de
# cobertura do Modo A, exemplo de celula lido da TABELA, exemplo de tecnica
# calculado pelo catalogo, limite de priorizados, "tipicamente 8-9" de
# `limits.py`, exemplo de reps/RPE dos defaults de prescricao) e RAG sem
# "9 a 10 series semanais" (meta semanal = a do macro-grupo).
# v9 (IA-06 b): so o RAG muda (template igual): HABILIDADES_TREINO/HIPERTROFIA/
# COSTAS sem percentual de carga de aquecimento/ajuste (vem de `session_loads`)
# e sem faixa de reps em prosa (remete aos defaults de prescricao).
# v10 (IA-05): MATERIAL DE REFERENCIA (RAG) logo depois das regras, antes dos
# dados do pedido (grupos, metas, tecnicas, tabela, catalogo) - prefixo
# cacheavel. Mesmo conteudo, outra ordem.
# v11 (modo de contagem do volume, Lote 5): o pedido ganha
# `volume_counting_mode`. Em `primary_only` (default) o texto e os dados sao os
# da v10, letra por letra. No modo ponderado a TABELA vem das faixas do modo, o
# catalogo traz `volume_contributions` (pesos do cadastro, resolvidos pelo
# motor) e a regra de contagem/checklist descreve a soma ponderada, com o
# limiar de sessao do secundario interpolado do motor.
# v12 (M97 F5, objetivo de treino): o pedido ganha `training_goal` (objetivo do
# Modelo) e `day_goals` (objetivo de cada dia; Modelo hibrido, ex.: 2 dias de
# forca em 5). Com objetivo nao-default, o prompt ganha a secao OBJETIVO POR DIA
# (regras de cada objetivo com todo numero interpolado dos donos:
# `defaults_for`, `training_safety`, `limits.heavy_compound_range`), o
# catalogo traz `is_compound` e o RAG traz o material do objetivo
# (`rag/markdown/OBJETIVOS/FORCA`, placeholders interpolados) na frente da
# base; o validador aplica o objetivo item a item (`goal_plan.py`: override,
# isolador do dia de forca em hipertrofia, tecnica/RPE/reps do objetivo,
# compostos do dia). Sem objetivo nao-default, texto, catalogo e RAG sao os da
# v11, letra por letra.
# v13 (M98 C5, cardio): com cardio pedido (`include_cardio` ou preferencias
# que falam de cardio; `cardio_plan.cardio_requested`), o prompt ganha a secao
# CARDIO NO MODELO (regras e envelope com todo numero interpolado dos donos
# `cardio_prescription`/`cardio_envelope`/`cardio_policy`/`hr_zones`, mais o
# recorte compacto de `CARDIO.md`) e o schema ganha `cardio_items` por slot;
# o validador passa cada prescricao pelo dono e reaplica o envelope do Modelo.
# Sem pedido de cardio, prompt e schema sao os da v12, letra por letra.
# v14 (27/10/2026, decisoes do usuario): na secao CARDIO NO MODELO, HIIT
# vetado so na VESPERA do dia de perna (no proprio dia, permitido depois da
# forca); a faixa do TSB segue interpolada (-25/-35 em `cardio_envelope`).
# Sem pedido de cardio, prompt e schema continuam os da v12, letra por letra.
# v15 (31/10/2026, escolha guiada pelo motor): no Modo B o catalogo vai com a
# referencia curta de cada opcao valida (`catalog_refs`, ex.: "E17") no lugar
# dos ids, e o item da saida tem so `catalog_ref` (enum por pedido) no lugar
# do trio; o motor monta o trio antes da validacao (`resolve_output`).
# `rest_seconds` saiu do schema nos dois modos (regra do motor: null). Prosa
# do Modo A, do objetivo e do cardio igual a da v14, menos o `rest_seconds`.
PROMPT_VERSION = "periodization-v15"
ENGINE_VERSION = "periodization-v8.0.0"

# Orcamento de tempo da chamada a LLM (secao 7.3): MENOR que o timeout da
# Lambda que roda o job, de proposito - o estouro precisa virar job `failed`
# com mensagem, nao Lambda morta (com 0 retries, `lambda_async_invoke_config.tf`,
# a Lambda morta so vira `worker_lost` no poll, timeout + 60 s depois).
# Derivado do timeout da `musclefit-plan-generator` (`async_jobs`, fonte unica
# com paridade contra o Terraform) menos a folga reservada para o resto do
# job: contexto (RAG + leituras no Aurora, inclusive a retomada do auto-pause),
# validacao deterministica (pior caso medido < 4 s) e gravacao do log/job.
# Era 90 s: o Claude Haiku 5.5 com raciocinio passa disso nos Modelos de 6
# dias (eval de 2026-10-30 e docs/ia/EVAL_PERIODIZACAO_2026-10-31.md).
PLAN_GENERATOR_FUNCTION = "musclefit-plan-generator"
LAMBDA_RESERVED_SECONDS = 30
LLM_TIMEOUT_SECONDS = LAMBDA_TIMEOUT_SECONDS[PLAN_GENERATOR_FUNCTION] - LAMBDA_RESERVED_SECONDS
# Parte da folga que fica para DEPOIS da chamada (validacao + log + escrita do
# job). O teto efetivo da chamada e o menor entre `LLM_TIMEOUT_SECONDS` e o
# que sobra da Lambda descontado o tempo ja gasto no contexto
# (`llm_timeout_for`): contexto lento (retomada do Aurora) encolhe a chamada,
# em vez de a Lambda morrer e o job so fechar como `worker_lost`.
POST_LLM_RESERVED_SECONDS = LAMBDA_RESERVED_SECONDS // 2
# Nome antigo, mantido para quem ja o le (mapa de fontes, testes).
OPENAI_TIMEOUT_SECONDS = LLM_TIMEOUT_SECONDS

MODEL_ENV_VAR = "OPENAI_MODEL_PERIODIZATION"
JOB_TYPE = "periodization"  # `async_jobs.JOB_TYPE_LAMBDA` (workout_plan_templates.py)


def export_for_frontend() -> dict[str, int]:
    """Secao `periodization_job` do `motor-constants.generated.json`: o tempo
    da chamada e o prazo em que o job `pending` vira `failed`/`worker_lost` no
    poll (`async_jobs.worker_lost_deadline_seconds`). A tela de geracao espera
    ate esse prazo, para nunca desistir de um job que ainda vai responder."""
    from ..async_jobs import worker_lost_deadline_seconds

    return {
        "llm_timeout_seconds": LLM_TIMEOUT_SECONDS,
        "job_deadline_seconds": int(worker_lost_deadline_seconds(JOB_TYPE) or 0),
    }


class PeriodizationError(RuntimeError):
    """Falha da periodizacao com mensagem pronta para o job `failed`.

    `detail` existe para o `lambda_plan_generator._error_message` (que le
    `getattr(exc, "detail", None)` antes de `str(exc)`) receber o texto exato -
    e, quando ha relatorio, o JSON do relatorio."""

    def __init__(self, message: str, payload: Optional[dict[str, Any]] = None):
        super().__init__(message)
        self.payload = payload
        self.detail = (
            json.dumps({"message": message, **payload}, ensure_ascii=False, default=str)
            if payload
            else message
        )


def llm_timeout_for(elapsed_seconds: float) -> float:
    """Teto da chamada depois de `elapsed_seconds` gastos no job (contexto)."""
    remaining = LAMBDA_TIMEOUT_SECONDS[PLAN_GENERATOR_FUNCTION] - POST_LLM_RESERVED_SECONDS - elapsed_seconds
    return min(float(LLM_TIMEOUT_SECONDS), remaining)


def resolve_model() -> str:
    """Modelo da periodizacao. SEM default: a env var vem do Terraform
    (`infra/lambda_ai_generators.tf`) e, sem ela, a geracao falha de forma limpa
    - job `failed` com mensagem de configuracao - em vez de cair num modelo
    qualquer e cobrar por uma saida que ninguem escolheu."""
    model = (os.environ.get(MODEL_ENV_VAR) or "").strip()
    if not model:
        raise PeriodizationError(
            f"{MODEL_ENV_VAR} nao configurada no backend: a geracao automatica de modelo "
            "esta indisponivel (nenhum dado foi alterado)."
        )
    return model


def run_periodization_draft(
    payload: dict[str, Any], user_id: Optional[int], generation_log_id: Optional[int] = None
) -> dict[str, Any]:
    """Resultado do job (secao 7.2): `{draft, validation_report, rationale,
    source_citations, generation_log_id, model, prompt_version}`.

    Levanta `PeriodizationError` quando o rascunho NAO e utilizavel (falha de
    configuracao, falha da OpenAI ou erro estrutural no relatorio) - o
    `lambda_plan_generator` transforma isso em job `failed`, com o relatorio
    dentro da mensagem quando ele existe."""
    started = time.monotonic()
    request = PeriodizationDraftIn(**(payload or {}))
    model = _resolve_model_or_fail(generation_log_id)

    with transaction() as conn:
        context = build_context(conn, request, user_id)
    prompt = build_prompt(context)
    logger.info(
        "periodizacao: prompt montado (%d caracteres, %d fontes RAG, modo %s)",
        len(prompt),
        len(request.rag_sources),
        request.volume_mode,
    )

    mode_b = request.volume_mode == "equipment_series"
    ref_index = catalog_refs.build_index(context) if mode_b else {}
    llm_timeout = llm_timeout_for(time.monotonic() - started)
    if llm_timeout < TEXT_RETRY_MIN_ATTEMPT_SECONDS:
        finish_log(generation_log_id, status=STATUS_FAILED, model=model)
        logger.error("periodizacao: contexto consumiu o orcamento da Lambda (%.1f s para a LLM)", llm_timeout)
        raise PeriodizationError(f"Falha na geracao pela OpenAI: {USER_MESSAGE_TIMEOUT}")
    try:
        result = call_openai_structured(
            prompt,
            model,
            output_schema(bool(context.get("cardio")), mode_b, catalog_refs.schema_enum(ref_index)),
            schema_name=SCHEMA_NAME,
            timeout=llm_timeout,
            feature="periodization",
            user_id=user_id,
            prompt_version=PROMPT_VERSION,
            cache_prefix=build_prompt_cache_prefix(context),
            anthropic_optional_first=ANTHROPIC_OPTIONAL_FIRST,
        )
    except Exception as exc:
        finish_log(generation_log_id, status=STATUS_FAILED, model=model)
        # SEG-12: a falha da OpenAI ja chega com mensagem generica (o
        # diagnostico foi para o log em `llm.error`); qualquer outra excecao
        # vai para o log e o job recebe so a mensagem generica.
        if isinstance(exc, OpenAIGenerationError):
            message = str(exc)
        else:
            logger.exception("periodizacao: falha inesperada na chamada a OpenAI")
            message = USER_MESSAGE_UNAVAILABLE
        raise PeriodizationError(f"Falha na geracao pela OpenAI: {message}") from exc

    # v15: a referencia escolhida vira o trio ANTES de qualquer validacao (o
    # validador e o rascunho veem o formato de sempre). Referencia
    # desconhecida deixa o trio vazio: o item sai como `ITEM_REMOVED` e o
    # complemento de volume cobre o grupo; a contagem vai ao relatorio.
    resolved, ref_stats = catalog_refs.resolve_output(result.data, ref_index)
    if ref_stats.total:
        logger.info(
            "periodizacao: referencias do catalogo total=%d resolvidas=%d desconhecidas=%d",
            ref_stats.total,
            ref_stats.resolved,
            len(ref_stats.unknown),
        )
    with transaction() as conn:
        draft, report, extras = validate_draft(conn, request, resolved, cardio=context.get("cardio"))
    report = catalog_refs.attach_to_report(report, ref_stats)

    status = STATUS_FAILED if report["errors"] else STATUS_SUCCEEDED
    finish_log(
        generation_log_id,
        status=status,
        model=result.model,
        validation_report=report,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
    )

    if report["errors"]:
        # Secao 14: so id numerico e reason_code no log estruturado - nunca a
        # instrucao do usuario nem o rascunho.
        log_event(
            EVENT_PERIODIZATION_VALIDATION_FAILED,
            user_id=user_id,
            engine_version=ENGINE_VERSION,
            reason_code=report["errors"][0]["code"],
            error_count=len(report["errors"]),
            generation_log_id=generation_log_id,
        )
        raise PeriodizationError(
            "O rascunho gerado nao passou na validacao deterministica.",
            {
                "validation_report": report,
                "generation_log_id": generation_log_id,
                "model": result.model,
                "prompt_version": PROMPT_VERSION,
            },
        )

    return {
        "draft": draft,
        "validation_report": report,
        "rationale": extras["rationale"],
        "source_citations": extras["source_citations"],
        "generation_log_id": generation_log_id,
        "model": result.model,
        "prompt_version": PROMPT_VERSION,
    }


def _resolve_model_or_fail(generation_log_id: Optional[int]) -> str:
    try:
        return resolve_model()
    except PeriodizationError:
        finish_log(generation_log_id, status=STATUS_FAILED)
        raise
