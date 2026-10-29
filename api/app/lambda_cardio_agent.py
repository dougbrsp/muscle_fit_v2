"""Entrypoint da Lambda `musclefit-cardio-agent` (imagem separada, sem Lambda
Web Adapter - invocada diretamente via `lambda:InvokeFunction`
`InvocationType=Event` por `musclefit-api-core`). Ver
docs/aws-migration-plan.md, secao 1.2 e Fase 3.

Evento esperado (payload publicado por `app/async_jobs.invoke_async` a
partir de `POST /cardio-recommendations/generate`):
    {"job_id": "...", "payload": {...}, "user_id": 1}

Roda a MESMA logica do antigo endpoint sincrono
(`cardio_generation_core.dispatch_cardio_generation` - gera via OpenAI E
persiste em cardio_recommendations) e grava o resultado (ou erro) no
DynamoDB via `app/async_jobs.py`.
"""

from __future__ import annotations

import logging

# Ver comentario equivalente em lambda_plan_generator.py: resolver segredos
# ANTES de importar qualquer modulo de negocio.
from .secrets_bootstrap import load_secrets

load_secrets()

from . import async_jobs
from .logging_config import configure_logging
from .cardio_generation_core import dispatch_cardio_generation

logger = logging.getLogger("musclefit.lambda_cardio_agent")


def handler(event, context):
    configure_logging()
    job_id = event.get("job_id")
    if not job_id:
        # Nunca logar o evento inteiro (payload com dados do usuario; IA-17).
        logger.error("Evento sem job_id, nada a gravar (chaves=%s)", sorted(event.keys()))
        return

    try:
        # M81: reivindica o job ANTES de chamar a OpenAI - entrega atrasada
        # pela fila de throttling (job ja fechado como worker_lost pelo poll)
        # ou duplicada nao roda nem cobra. Ver app/async_jobs.py.
        if not async_jobs.claim_job(job_id):
            logger.warning("Job %s não está mais pendente (worker_lost ou entrega duplicada); nada a fazer", job_id)
            return
        payload = event.get("payload") or {}
        user_id = event.get("user_id")
        result = dispatch_cardio_generation(payload, user_id)
        async_jobs.mark_done(job_id, result)
    except Exception as exc:  # noqa: BLE001 - job assincrono: qualquer falha vira "failed" no DynamoDB
        logger.exception("Falha ao gerar recomendacao de cardio (job_id=%s)", job_id)
        async_jobs.mark_failed(job_id, _error_message(exc))


def _error_message(exc: Exception) -> str:
    detail = getattr(exc, "detail", None)
    if detail is not None:
        return str(detail)
    return str(exc)
