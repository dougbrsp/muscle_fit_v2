"""Entrypoint da Lambda `musclefit-workout-ai` (mesma imagem de container
`musclefit-ai-generators` de `musclefit-plan-generator`/
`musclefit-cardio-agent`, so um `image_config.command` diferente - sem
Lambda Web Adapter, invocada diretamente via `lambda:InvokeFunction`
`InvocationType=Event` por `musclefit-api-core`, nao via API Gateway). Ver
docs/aws-migration-plan.md, secao 1.2 e Fase 3.

Evento esperado (payload publicado por `app/async_jobs.invoke_async` a
partir de `POST /exercises/evaluate-set`, `POST /exercises/{id}/summarize`,
`POST /activities/{id}/ai-summary/generate`, `POST /workouts-analysis/generate`
ou `POST /insights/generate`):
    {"job_id": "...", "job_type": "workout-ai-evaluate-set" | "workout-ai-exercise-summary" | "workout-ai-activity-summary" | "workouts_analysis" | "insights" | "nutrition-meal-parse", "payload": {...}, "user_id": 1}

A leitura da refeicao por IA (nutricao L5b, `POST /nutrition/meals/parse`) traz
tambem `"ai_usage_id"`: a linha `pending` do `ai_usage_log` que a rota abriu. O
worker da feature a fecha em todos os caminhos dele; se o handler falhar ANTES
de despachar (reivindicacao do job), ela e fechada aqui como `failed`.

Roda a MESMA logica de dispatch dos 3 antigos endpoints sincronos
(`workout_ai_generation_core.dispatch_workout_ai_generation`) e grava o
resultado (ou erro) no DynamoDB via `app/async_jobs.py`. Sem resposta HTTP -
o chamador (api-core) ja recebeu 202 antes desta Lambda sequer comecar a
rodar.
"""

from __future__ import annotations

import logging

# Resolve segredos (OPENAI_API_KEY, API_TOKEN, SECRET_ENCRYPTION_KEY) do
# Secrets Manager para env vars ANTES de importar qualquer modulo de negocio
# - mesmo comentario/motivo de lambda_plan_generator.py/lambda_cardio_agent.py.
from .secrets_bootstrap import load_secrets

load_secrets()

from . import async_jobs
from . import nutrition_ai_quota
from .domain.nutrition.ai_usage import AiUsageStatus
from .domain.nutrition.meal_parse import ERR_WORKER_FAILED
from .logging_config import configure_logging
from .workout_ai_generation_core import dispatch_workout_ai_generation

logger = logging.getLogger("musclefit.lambda_workout_ai")


def handler(event, context):
    configure_logging()
    job_id = event.get("job_id")
    if not job_id:
        # Nunca logar o evento inteiro (payload com dados do usuario).
        logger.error(
            "Evento sem job_id, nada a gravar (chaves=%s, job_type=%s)",
            sorted(event.keys()),
            event.get("job_type"),
        )
        return

    job_type = event.get("job_type")
    ai_usage_id = event.get("ai_usage_id")
    despachado = False
    try:
        # M81: reivindica o job ANTES de chamar a OpenAI - entrega atrasada
        # pela fila de throttling (job ja fechado como worker_lost pelo poll)
        # ou duplicada nao roda nem cobra. Ver app/async_jobs.py.
        if not async_jobs.claim_job(job_id):
            logger.warning("Job %s não está mais pendente (worker_lost ou entrega duplicada); nada a fazer", job_id)
            return
        if not job_type:
            raise ValueError("Evento sem job_type.")
        payload = event.get("payload") or {}
        user_id = event.get("user_id")
        despachado = True
        result = dispatch_workout_ai_generation(job_type, payload, user_id, ai_usage_id=ai_usage_id)
        async_jobs.mark_done(job_id, result)
    except Exception as exc:  # noqa: BLE001 - job assincrono: qualquer falha vira "failed" no DynamoDB
        logger.exception("Falha ao processar job de IA do modo treino (job_id=%s, job_type=%s)", job_id, job_type)
        if ai_usage_id and not despachado:
            # O worker da feature nao chegou a rodar: a linha do uso fecha aqui
            # (`finish_usage` nunca levanta).
            nutrition_ai_quota.finish_usage(ai_usage_id, status=AiUsageStatus.FAILED, error_code=ERR_WORKER_FAILED)
        async_jobs.mark_failed(job_id, _error_message(exc))


def _error_message(exc: Exception) -> str:
    # HTTPException tem .detail (pode ser str ou dict); outras excecoes usam str(exc).
    detail = getattr(exc, "detail", None)
    if detail is not None:
        return str(detail)
    return str(exc)
