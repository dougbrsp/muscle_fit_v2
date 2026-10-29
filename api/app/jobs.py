"""GET /jobs/{job_id} - consulta o estado de um job assincrono (padrao 202 +
job_id + polling, ver app/async_jobs.py e docs/aws-migration-plan.md, secao
1.2). Usado pelo frontend para dar poll depois de POST /workout-plans/generate
ou POST /cardio-recommendations/generate."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from . import async_jobs
from .domain.nutrition import ai_usage
from .security import Identity, get_current_identity

logger = logging.getLogger("fitness.jobs")

router = APIRouter(prefix="/jobs", tags=["jobs"], dependencies=[Depends(get_current_identity)])


@router.get("/{job_id}")
def get_job_status(job_id: str, identity: Identity = Depends(get_current_identity)):
    """M65: so o usuario que criou o job o le. Job de outro usuario (inclusive
    para admin - nenhuma tela de admin consulta job alheio) ou sem dono
    (criado antes do M65) responde o MESMO 404 de job inexistente/expirado,
    para nao revelar que o id existe."""
    job = async_jobs.get_job(job_id, owner_user_id=identity.user_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job não encontrado (ou expirado - jobs somem após algumas horas).")
    if job.get("job_type") == "periodization" and job.get("error_code") == async_jobs.WORKER_LOST_ERROR_CODE:
        _sweep_periodization_logs()
    if ai_usage.feature_for_job_type(job.get("job_type")) and job.get("error_code") == async_jobs.WORKER_LOST_ERROR_CODE:
        _sweep_nutrition_ai_usage()
    return job


def _sweep_periodization_logs() -> None:
    """M73: a periodizacao tambem tem a linha de auditoria em
    `template_generation_logs`; fecha as `pending` orfas junto com o job.
    Best-effort - o poll responde de qualquer jeito."""
    try:
        from .db import transaction
        from .periodization.logs import sweep_stale_pending_logs

        with transaction() as conn:
            sweep_stale_pending_logs(conn)
    except Exception:  # noqa: BLE001
        logger.exception("Falha no sweep de template_generation_logs pendentes (M73)")


def _sweep_nutrition_ai_usage() -> None:
    """Nutricao (L5a/L5b): o job de uma feature de IA da nutricao fechado como
    `worker_lost` tambem fecha as linhas `pending` vencidas do `ai_usage_log`
    (`nutrition_ai_quota.sweep_stale_pending`, global e idempotente). Best-effort."""
    try:
        from .db import transaction
        from .nutrition_ai_quota import sweep_stale_pending

        with transaction() as conn:
            sweep_stale_pending(conn)
    except Exception:  # noqa: BLE001
        logger.exception("Falha no sweep de ai_usage_log pendentes (nutricao)")
