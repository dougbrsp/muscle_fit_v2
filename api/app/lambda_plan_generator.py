"""Entrypoint da Lambda `musclefit-plan-generator` (imagem separada, sem
Lambda Web Adapter - invocada diretamente via `lambda:InvokeFunction`
`InvocationType=Event` por `musclefit-api-core`, nao via API Gateway). Ver
docs/aws-migration-plan.md, secao 1.2 e Fase 3.

Evento esperado (payload publicado por `app/async_jobs.invoke_async` a
partir de `POST /workout-plans/generate`):
    {"job_id": "...", "payload": {...WorkoutPlanGenerateIn...}, "user_id": 1}

Desde o PR-16 esta Lambda atende DOIS tipos de job, escolhidos por `job_kind`
(ausente = `plan`, o formato historico acima, intocado):
    {"job_id": "...", "job_kind": "periodization", "payload": {...PeriodizationDraftIn...},
     "user_id": 1, "generation_log_id": 55}
A periodizacao (`app/periodization/`) reaproveita imagem, timeout e IAM
desta funcao - ver PLANO_20260912.md, secao 7.1, passo 4. (O timeout era de
120 s na epoca; passou a 180 s com os cards ilustrados, ver abaixo.)

E desde a melhoria M4 atende um TERCEIRO `job_kind`, pelo mesmo motivo:
    {"job_id": "...", "job_kind": "exercise-description", "exercise_id": 42}
a descricao de execucao de um exercicio do catalogo, enfileirada pelo endpoint
de briefing quando a tela abre um exercicio que ainda nao tem texto - ver
`app/exercise_description_autogen.py`.

E, com a nutricao (Lote L6), um QUINTO, a leitura da foto do rotulo:
    {"job_id": "...", "job_kind": <ai_usage.LABEL_PHOTO_JOB_TYPE>, "user_id": 1,
     "ai_usage_id": 9, "s3_key": "nutrition-labels/1/<uuid>.jpg"}
uma chamada a OpenAI com imagem (ate ~90 s) e a revisao do motor de nutricao,
enfileirada por `POST /nutrition/foods/label-photo` - ver
`app/nutrition_label_ai.py`. Nada e gravado no banco alem do `ai_usage_log`.

E, com os cards ilustrados de exercicios, um QUARTO:
    {"job_id": "...", "job_kind": "exercise-card", "generation_id": 7, "exercise_id": 42}
a imagem do card (OpenAI Images, ate ~2 min - por isso o timeout desta funcao
passou a 180 s - seguida da composicao do card final com Pillow, fontes e logo
versionados), enfileirada por `POST /exercises/{id}/card-generations` - ver
`app/exercise_card_jobs.py`.

Roda a MESMA logica de dispatch do antigo endpoint sincrono
(`plan_generation_core.dispatch_plan_generation`) e grava o resultado (ou
erro) no DynamoDB via `app/async_jobs.py`. Sem resposta HTTP - o chamador
(api-core) ja recebeu 202 antes desta Lambda sequer comecar a rodar.
"""

from __future__ import annotations

import logging

# Resolve segredos (API_TOKEN, OPENAI_API_KEY, SECRET_ENCRYPTION_KEY) do
# Secrets Manager para env vars ANTES de importar qualquer modulo de negocio
# - evita reintroduzir o tipo de bug de "constante de modulo capturada antes
# do secret existir" (achado real: workout_plan_ai.py tinha isso ate
# 2026-09-04 - ver docs/aws-migration-plan.md, Fase 3).
from .secrets_bootstrap import load_secrets

load_secrets()

from . import async_jobs
from .domain.nutrition.ai_usage import LABEL_PHOTO_JOB_TYPE
from .logging_config import configure_logging
from .models import WorkoutPlanGenerateIn
from .plan_generation_core import dispatch_plan_generation

logger = logging.getLogger("musclefit.lambda_plan_generator")

# Tipos de job que esta Lambda atende. `job_kind` AUSENTE = geracao de ficha,
# o comportamento historico (eventos publicados por `POST
# /workout-plans/generate` nao mandam o campo, e continuam funcionando sem
# nenhuma mudanca no chamador).
JOB_KIND_PLAN = "plan"
JOB_KIND_PERIODIZATION = "periodization"
# M4: descricao de execucao de UM exercicio do catalogo, enfileirada pelo
# briefing quando o exercicio ainda nao tem texto
# (`app/exercise_description_autogen.py`). Mesmo racional da periodizacao:
# "uma chamada longa a OpenAI + uma escrita no banco" - exatamente o que esta
# funcao ja e. Nada de infra nova.
JOB_KIND_EXERCISE_DESCRIPTION = "exercise-description"
# Cards ilustrados: UMA imagem por job, com reserva atomica PENDING ->
# GENERATING (redelivery do invoke nao cobra de novo) - ver
# `app/exercise_card_jobs.py`.
JOB_KIND_EXERCISE_CARD = "exercise-card"
# Nutricao L6: leitura da foto do rotulo. O job type e o do dono
# (`domain/nutrition/ai_usage`); a api-core o manda como `job_kind`.
JOB_KIND_LABEL_PHOTO = LABEL_PHOTO_JOB_TYPE


def handler(event, context):
    configure_logging()
    job_id = event.get("job_id")
    if not job_id:
        # Nunca logar o evento inteiro (payload com dados do usuario; IA-17).
        logger.error(
            "Evento sem job_id, nada a gravar (chaves=%s, job_kind=%s)",
            sorted(event.keys()),
            event.get("job_kind"),
        )
        return

    try:
        # M81: reivindica o job ANTES de qualquer chamada de IA. Entrega
        # atrasada pela fila de throttling (job ja fechado como worker_lost
        # pelo poll) ou duplicada (at-least-once) nao roda nem cobra. Os
        # cards (`exercise-card`) mantem a reserva atomica propria no banco;
        # o claim so soma protecao.
        if not async_jobs.claim_job(job_id):
            logger.warning("Job %s não está mais pendente (worker_lost ou entrega duplicada); nada a fazer", job_id)
            return
        result = _run(event)
        async_jobs.mark_done(job_id, result)
    except Exception as exc:  # noqa: BLE001 - job assincrono: qualquer falha vira "failed" no DynamoDB
        logger.exception("Falha no job da plan-generator (job_id=%s)", job_id)
        async_jobs.mark_failed(job_id, _error_message(exc))


def _run(event) -> dict:
    """Despacho por `job_kind` (PR-16). A periodizacao reaproveita esta Lambda -
    mesma imagem, mesmo timeout (180 s desde os cards ilustrados; era 120 s),
    mesma IAM - em vez de criar uma funcao nova: ela tambem e "uma chamada longa a OpenAI + escrita no banco",
    exatamente o que esta funcao ja e. Import tardio do modulo da periodizacao
    para nao pesar o cold start da geracao de ficha, que e o caminho quente."""
    job_kind = event.get("job_kind") or JOB_KIND_PLAN
    user_id = event.get("user_id")

    if job_kind == JOB_KIND_PERIODIZATION:
        from .periodization.core import run_periodization_draft

        return run_periodization_draft(event.get("payload") or {}, user_id, event.get("generation_log_id"))

    if job_kind == JOB_KIND_EXERCISE_DESCRIPTION:
        from .exercise_description_autogen import run_generation_job

        return run_generation_job(event.get("exercise_id"), model=event.get("model"))

    if job_kind == JOB_KIND_EXERCISE_CARD:
        from .exercise_card_jobs import run_generation_job as run_exercise_card_job

        return run_exercise_card_job(event)

    if job_kind == JOB_KIND_LABEL_PHOTO:
        from .nutrition_label_ai import run_label_job

        return run_label_job(event)

    if job_kind != JOB_KIND_PLAN:
        raise ValueError(f"job_kind desconhecido: {job_kind!r}")

    # M16: o `job_id` fica ao alcance da auditoria, que o grava em
    # `generation_runs` para ligar a linha ao item do DynamoDB que expira em
    # 6h. Vai POR FORA da chamada de proposito: acrescentar um argumento a
    # `dispatch_plan_generation` faria a auditoria mudar o contrato da geracao
    # - e a primeira versao deste PR provou o estrago (um chamador com a
    # aridade antiga virou `TypeError` aqui dentro, e o handler marcou como
    # `failed` um job cuja geracao correu bem).
    _remember_job_id(event.get("job_id"))
    return dispatch_plan_generation(WorkoutPlanGenerateIn(**(event.get("payload") or {})), user_id)


def _remember_job_id(job_id) -> None:
    """Best-effort, no padrao de `_attach_volume_coverage_warnings`: import
    tardio e excecao engolida. Sem o `job_id` a linha de auditoria nasce sem
    a procedencia do job - a ficha, essa, e gerada de qualquer forma."""
    try:
        from .generation.generation_runs import set_current_job_id

        set_current_job_id(job_id)
    except Exception:  # noqa: BLE001 - auditoria nunca derruba a geracao (M16)
        logger.exception("Falha ao registrar o job_id para a auditoria (job_id=%r)", job_id)


def _error_message(exc: Exception) -> str:
    # HTTPException tem .detail (pode ser str ou dict); outras excecoes usam str(exc).
    detail = getattr(exc, "detail", None)
    if detail is not None:
        return str(detail)
    return str(exc)
