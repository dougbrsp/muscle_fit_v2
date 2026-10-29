from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from . import async_jobs
from .models import WorkoutsAnalysisGenerateIn
from .security import Identity, get_current_identity
from .workouts_analysis_core import JOB_TYPE_WORKOUTS_ANALYSIS

router = APIRouter(prefix="/workouts-analysis", tags=["workouts-analysis"])


@router.post("/generate", status_code=202)
def generate_workouts_analysis(
    payload: WorkoutsAnalysisGenerateIn | None = None,
    identity: Identity = Depends(get_current_identity),
):
    """Assincrono desde a Fase 3 da migracao AWS (202 + job_id + polling via
    GET /jobs/{job_id}) - a geracao em si
    (`workouts_analysis_core.dispatch_workouts_analysis`, mesma logica exata
    do endpoint sincrono anterior) roda na Lambda `musclefit-workout-ai` via
    `workout_ai_generation_core.dispatch_workout_ai_generation`, invocada
    aqui de forma assincrona (`InvocationType=Event`). Mesmo motivo dos 3
    endpoints de IA do modo treino (POST /exercises/evaluate-set etc.) - a
    chamada a OpenAI e incompativel com o teto de 29s do API Gateway, ver
    docs/aws-migration-plan.md, secao 1.2.

    Gera, sob demanda e sem persistir nada, a analise de evolucao de treinos
    via OpenAI para a tela /treinos. Reaproveita exatamente os mesmos
    services usados pela secao "Evolucao dos Treinos" do Relatorio em PDF
    (`/relatorios`) - `DashboardAggregatorService.build_training_evolution_payload`,
    `ReportDataService.build_rag_context` e `report_ai.analyze_training_evolution`."""
    days = (payload or WorkoutsAnalysisGenerateIn()).days
    job_id = async_jobs.create_job(
        JOB_TYPE_WORKOUTS_ANALYSIS,
        request={"payload": {"days": days}, "user_id": identity.user_id},
        owner_user_id=identity.user_id,
    )
    async_jobs.invoke_async(
        "WORKOUT_AI_FUNCTION_NAME",
        {
            "job_id": job_id,
            "job_type": JOB_TYPE_WORKOUTS_ANALYSIS,
            "payload": {"days": days},
            "user_id": identity.user_id,
        },
    )
    return JSONResponse(status_code=202, content={"job_id": job_id, "status": "pending"})
