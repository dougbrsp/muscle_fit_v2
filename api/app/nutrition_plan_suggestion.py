"""Sugestão de metas por IA: a rota que dispara o job (nutrição N6, decisão D3). Vive na api-core
(`{proxy+}`); a IA roda no worker da `musclefit-workout-ai` (`nutrition_plan_suggestion_ai.py`).

Rotas (prefixo `/nutrition/plan-suggestion`; `/plans/suggestion` colidiria com `/plans/{plan_id}`):
- `POST ""`       `PlanSuggestionIn` -> **202** `{job_id, status, poll_timeout_seconds, quota}`. Ordem: acesso
                  ao aluno (403/404) -> perfil suficiente para o motor (422, não gasta cota) -> reserva da cota
                  (429 `ai_quota_exceeded`) -> job no DynamoDB -> vínculo job × linha -> invoke assíncrono.
                  Falha ao despachar fecha a linha como `failed` e responde 503.
- `GET  "/usage"` uso da cota hoje (`{feature, limit, used, remaining, allowed}`).

O resultado chega por `GET /jobs/{job_id}`, que só devolve ao dono do job (M65). Nada é gravado aqui: a tela
preenche o formulário como rascunho, mostra a prévia do motor (`POST /nutrition/plan-preview`) e a pessoa salva
pelo `POST`/`PUT /nutrition/plans` de sempre.

A cota é de QUEM PEDE (o admin/PT que pede para um aluno gasta a própria cota), como nas outras features. O pedido
gravado no DynamoDB (`request`, auditoria) só leva o aluno; o estado do formulário vai no evento da Lambda.

Regra dos motores (mapa §84): campos, faixas e revisão de `domain/nutrition/plan_suggestion`; cota, feature e job
type de `ai_usage`; o prazo do polling é o do worker perdido do M73. Esta camada só orquestra.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from . import async_jobs, nutrition_ai_quota
from .db import get_conn
from .db_helpers import connect_or_503
from .domain.nutrition import labels as nutrition_labels
from .domain.nutrition import plan_suggestion
from .domain.nutrition.ai_usage import PLAN_SUGGESTION_JOB_TYPE, AiFeature, AiUsageStatus
from .domain.nutrition.daily_target import missing_subject_data
from .intelligence.dashboard.time_windows import user_today_for
from .nutrition_access import fetch_target_user, plan_actor, resolve_target_user
from .nutrition_plan_suggestion_ai import PROMPT_VERSION
from .nutrition_schema import require_nutrition_tables
from .nutrition_targets_service import compute_day, load_user_inputs
from .request_models import PlanSuggestionIn
from .security import Identity

logger = logging.getLogger("musclefit.nutrition.plan_suggestion")

router = APIRouter(prefix="/nutrition/plan-suggestion", tags=["nutrition"])

FEATURE = AiFeature.PLAN_SUGGESTION


def _error(status: int, code: str, **extra: Any) -> HTTPException:
    return HTTPException(
        status_code=status,
        detail={"error": code, "message": nutrition_labels.limitation_label(code), **extra},
    )


def _missing_subject_codes(conn: Any, cur: Any, aluno: int) -> tuple[str, ...]:
    """O que falta no perfil para o motor calcular (TMB, gasto): sem isso a IA não tem base, e não se gasta cota."""
    hoje = user_today_for(conn, aluno)
    comp = compute_day(load_user_inputs(cur, aluno, hoje, hoje, hoje), plan_suggestion.reference_settings(), hoje, hoje)
    return missing_subject_data(comp.target)


@router.get("/usage")
def plan_suggestion_usage(identity: Identity = Depends(plan_actor)):
    with connect_or_503(get_conn) as conn:
        return nutrition_ai_quota.usage_today(conn, user_id=int(identity.user_id), feature=FEATURE).to_dict()


@router.post("", status_code=202)
def suggest_plan(payload: PlanSuggestionIn, identity: Identity = Depends(plan_actor)):
    aluno = resolve_target_user(identity, payload.user_id)
    solicitante = int(identity.user_id)

    with connect_or_503(get_conn) as conn:
        with conn.cursor() as cur:
            require_nutrition_tables(cur)
            if aluno != solicitante:
                fetch_target_user(cur, aluno)
            faltas = _missing_subject_codes(conn, cur, aluno)
    if faltas:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "nutrition_subject_incomplete",
                "message": "Faltam dados do perfil para calcular a meta de hoje.",
                "missing": nutrition_labels.limitation_items(faltas),
            },
        )

    usage_id = nutrition_ai_quota.open_usage(user_id=solicitante, feature=FEATURE, prompt_version=PROMPT_VERSION)
    try:
        job_id = async_jobs.create_job(
            PLAN_SUGGESTION_JOB_TYPE,
            request={"target_user_id": aluno, "ai_usage_id": usage_id},
            owner_user_id=solicitante,
        )
        nutrition_ai_quota.link_job(usage_id, job_id)
        async_jobs.invoke_async(
            "WORKOUT_AI_FUNCTION_NAME",
            {
                "job_id": job_id,
                "job_type": PLAN_SUGGESTION_JOB_TYPE,
                "payload": {"target_user_id": aluno, "current": payload.current.model_dump()},
                "user_id": solicitante,
                "ai_usage_id": usage_id,
            },
        )
    except Exception as exc:  # noqa: BLE001 - DynamoDB ou invoke fora: o job nunca vai rodar
        logger.exception("Falha ao despachar a sugestão de metas (ai_usage_id=%s)", usage_id)
        nutrition_ai_quota.finish_usage(
            usage_id, status=AiUsageStatus.FAILED, error_code=plan_suggestion.ERR_SUGGESTION_DISPATCH_FAILED
        )
        raise _error(503, plan_suggestion.ERR_SUGGESTION_DISPATCH_FAILED) from exc

    return {
        "job_id": job_id,
        "status": "pending",
        "poll_timeout_seconds": async_jobs.worker_lost_deadline_seconds(PLAN_SUGGESTION_JOB_TYPE),
    }


__all__ = ["plan_suggestion_usage", "router", "suggest_plan"]
