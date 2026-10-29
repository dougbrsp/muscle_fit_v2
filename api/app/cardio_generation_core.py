"""Logica de geracao + persistencia de recomendacao de cardio (`POST
/cardio-recommendations/generate`), extraida de `cardio_recommendations.py`
para ser reutilizavel tanto pelo endpoint FastAPI (que agora so cria o job e
retorna 202) quanto pelo handler da Lambda `musclefit-cardio-agent`
(`lambda_cardio_agent.py`) - ver docs/aws-migration-plan.md, Fase 3, padrao
202 + job_id + DynamoDB. Mesmo comportamento exato do endpoint sincrono
anterior (mesmas excecoes - `HTTPException`, `CardioAgentGenerationError`)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException

from .cardio_agent_ai import CardioAgentGenerationError, generate_cardio_recommendation
from .cardio_recommendations import _connect, _insert_recommendation
from .intelligence.report_data_service import CARDIO_CONTEXT_MAX_DAYS

logger = logging.getLogger(__name__)


def dispatch_cardio_generation(payload: dict[str, Any], user_id: int) -> dict[str, Any]:
    """Corpo exato do antigo `generate_and_save_cardio_recommendation`
    sincrono - so parametrizado por `user_id` em vez de `Identity` do
    FastAPI. Sem DDL: o schema vem da migracao 025."""
    payload = payload or {}
    # A rota ja valida `days` (`CardioGenerateIn`, IA-10); o teto aqui cobre
    # quem invoca a Lambda direto com um payload sem schema.
    days = min(int(payload.get("days") or 14), CARDIO_CONTEXT_MAX_DAYS)
    proposed_workout = payload.get("proposed_workout")
    # v3 (M98 C3a): sem `reference_date`, o 1o dia e o "hoje" do USUARIO,
    # resolvido no contexto (`time_windows`) - nunca o `date.today()` da
    # Lambda (UTC), que depois das 21h de Brasilia ja e o dia seguinte.
    reference_date = payload.get("reference_date")
    reference_time = payload.get("reference_time")
    workout_plan_id = payload.get("workout_plan_id")

    if workout_plan_id:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM workout_plans WHERE id = %s AND user_id = %s",
                    (workout_plan_id, user_id),
                )
                if not cur.fetchone():
                    raise HTTPException(status_code=404, detail="Plano de treino não encontrado.")
        finally:
            conn.close()

    try:
        result = generate_cardio_recommendation(
            days=days,
            proposed_workout=proposed_workout,
            reference_date=reference_date,
            reference_time=reference_time,
            user_id=user_id,
            workout_plan_id=workout_plan_id,
        )
    except CardioAgentGenerationError as exc:
        logger.exception("Falha na geracao da recomendacao de cardio.")
        raise HTTPException(status_code=503, detail="Não foi possível gerar a recomendação de cardio agora.") from exc

    recommendation = result["recommendation_json"]
    reference_date = reference_date or recommendation.get("reference_date")
    try:
        new_id = _insert_recommendation(
            {
                "reference_date": reference_date,
                "workout_plan_id": workout_plan_id,
                "model": result["model"],
                "context_snapshot": result["context_snapshot"],
                "recommendation_markdown": result["recommendation_markdown"],
                # Migracao 098: JSON estruturado + versao do prompt.
                "recommendation_json": recommendation,
                "prompt_version": result["prompt_version"],
            },
            user_id,
        )
    except Exception as exc:
        logger.exception("Falha ao salvar a recomendacao de cardio.")
        raise HTTPException(status_code=500, detail="Erro interno ao processar a solicitação.") from exc

    return {
        "id": new_id,
        "model": result["model"],
        "reference_date": reference_date,
        "workout_plan_id": workout_plan_id,
        "prompt_version": result["prompt_version"],
        "recommendation_markdown": result["recommendation_markdown"],
        "recommendation_json": recommendation,
    }
