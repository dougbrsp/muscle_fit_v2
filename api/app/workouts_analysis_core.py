"""Logica de geracao da analise de evolucao de treinos (`POST
/workouts-analysis/generate`), extraida de `workouts_analysis.py` para ser
reutilizavel tanto pelo endpoint FastAPI (que agora so valida payload, cria o
job e retorna 202) quanto pelo handler da Lambda `musclefit-workout-ai`
(`lambda_workout_ai.py`, via `workout_ai_generation_core.
dispatch_workout_ai_generation`) - ver docs/aws-migration-plan.md, Fase 3,
mesmo padrao 202 + job_id + DynamoDB ja usado por `plan_generation_core.py`/
`cardio_generation_core.py`/`workout_ai_generation_core.py`.

Mantido o comportamento exato do endpoint sincrono anterior: reaproveita os
mesmos services usados pela secao "Evolucao dos Treinos" do Relatorio em PDF
(`/relatorios`) - `DashboardAggregatorService.build_training_evolution_payload`,
`ReportDataService.build_rag_context` e `report_ai.analyze_training_evolution`.

NOTA: `report_ai.analyze_training_evolution` monta um prompt que ainda
carrega um resquicio de texto de contexto de PDF (herdado da geracao do
Relatorio em PDF) - preservado sem alteracao nesta migracao, conforme
solicitado."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from . import report_ai
from .intelligence.dashboard_aggregator_service import DashboardAggregatorService
from .intelligence.report_data_service import ReportDataService
from .report_ai import ReportGenerationError

JOB_TYPE_WORKOUTS_ANALYSIS = "workouts_analysis"


def dispatch_workouts_analysis(payload: dict[str, Any], user_id: int) -> dict[str, Any]:
    """Corpo exato do antigo `generate_workouts_analysis` sincrono - so
    parametrizado por `user_id` em vez de `Identity` do FastAPI."""
    payload = payload or {}
    days = payload.get("days") or 90

    training = DashboardAggregatorService().build_training_evolution_payload(days=days, user_id=user_id)
    rag_context = ReportDataService().build_rag_context(training)

    try:
        insight = report_ai.analyze_training_evolution(
            training, days=days, rag_context=rag_context, user_id=user_id
        )
    except ReportGenerationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {"model": report_ai.OPENAI_MODEL, "insight": insight, "training": training}
