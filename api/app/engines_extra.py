"""Rotas de motor deterministico que nao se encaixam nos routers de
`/analytics/*` (analytics.py/dashboard_modules.py/dashboard_intelligence.py)
mas sao igualmente parte do escopo da Lambda `musclefit-engines` - nenhuma
chamada a OpenAI, so leitura de dados + regra de negocio deterministica
(`services.suggested_progression_for_mapping` /
`services.suggested_set_count_for_substitution`, motor de progressao/volume
ja usado internamente por `analytics.py::dashboard-progression-suggestion`).

Extraido de `app/main.py` (endpoints originais `GET
/exercises/suggested-progression` e `GET
/exercises/suggested-set-count-for-substitution`) na extracao da Lambda 2
(`musclefit-engines`) - ver docs/aws-migration-plan.md e
infra/lambda_engines.tf. Mesmos paths publicos, mesmo comportamento; so o
backend do API Gateway muda (aponta direto para musclefit-engines em vez de
musclefit-api-core - ver infra/api_gateway.tf)."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from . import services
from .observability import EVENT_SAFETY_CLAMP, log_event
from .security import Identity, get_current_identity

router = APIRouter(tags=["exercises-engine"], dependencies=[Depends(get_current_identity)])


@router.get("/exercises/suggested-progression")
def get_suggested_progression(
    mapping_id: int = Query(...),
    equipment_id: Optional[int] = Query(default=None, ge=1),
    session_id: Optional[int] = Query(default=None, ge=1),
    workout_plan_exercise_id: Optional[int] = Query(default=None, ge=1),
    training_goal: Optional[str] = Query(default=None, max_length=32),
    identity: Identity = Depends(get_current_identity),
):
    """Sugestao de reps_target/load_target/rpe_target para um exercicio fora da
    geracao de ficha (exercicio extra em /planos-treino, treino livre e troca de
    equipamento no Modo Treino).

    M76 (Lote 1a): a decisao e do motor v2 via
    `services.suggested_progression_for_mapping` -> `prescription_service`,
    ja composta com a seguranca do par e o contrato de recuperacao projetado
    para agora. `equipment_id` (= `gym_equipment.id`, mandado pelo Modo Treino
    ao trocar de equipamento) escolhe a trilha do par; sem ele vale a trilha
    executada mais recentemente do exercicio. `effective_rpe_ceiling`,
    `failure_allowed` e `clamps` saem sempre (antes, so com `equipment_id`).

    M97-D2 (troca de par no Modo Treino): `session_id`,
    `workout_plan_exercise_id` (item trocado) e `training_goal` (objetivo do
    item). O par novo sai no objetivo do item (o servidor prefere o resolvido
    do item da ficha da sessao ao do pedido); numa sessao de deload, ja com a
    prescricao de deload desde a 1a serie (dono `domain/deload.py`). Item de
    hipertrofia ou pedido sem esses parametros: a resposta de sempre."""
    result = services.suggested_progression_for_mapping(
        mapping_id,
        user_id=identity.user_id,
        gym_equipment_id=equipment_id,
        session_id=session_id,
        workout_plan_exercise_id=workout_plan_exercise_id,
        item_training_goal=training_goal,
    )
    if not result:
        raise HTTPException(status_code=404, detail="Mapeamento de exercício não encontrado.")
    for clamp in result.get("clamps") or []:
        log_event(
            EVENT_SAFETY_CLAMP,
            user_id=int(identity.user_id),
            exercise_id=int(mapping_id),
            field=clamp.get("field"),
            from_value=clamp.get("from"),
            to_value=clamp.get("to"),
            reason_code=clamp.get("reason"),
        )
    return result


@router.get("/exercises/suggested-set-count-for-substitution")
def get_suggested_set_count_for_substitution(
    original_mapping_id: int = Query(...),
    original_sets_count: int = Query(..., ge=1),
    new_mapping_id: int = Query(...),
    identity: Identity = Depends(get_current_identity),
):
    """Sugestao de quantas series fazer em um exercicio SUBSTITUTO escolhido em
    pleno treino (Modo Treino -> EquipmentSubstituteModal.tsx), para se
    aproximar do mesmo estimulo muscular que o exercicio original planejado
    geraria no grupo muscular primario dele (ver
    services.suggested_set_count_for_substitution)."""
    result = services.suggested_set_count_for_substitution(
        original_mapping_id, original_sets_count, new_mapping_id
    )
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Exercício original não encontrado ou sem grupo muscular primário cadastrado.",
        )
    return result
