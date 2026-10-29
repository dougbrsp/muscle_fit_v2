from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, Response

from . import async_jobs, deload_periods, services, workout_plan_edit_rules, workout_plan_equipment
from .db import transaction
from .generation.template_request import resolve_template_and_slot
from .models import PlanExerciseEquipmentSubstituteIn, WorkoutPlanGenerateIn, WorkoutPlanIn
from .muscle_map import render_muscle_map_png
from .security import Identity, get_current_identity

router = APIRouter(tags=["workout-plans"], dependencies=[Depends(get_current_identity)])


@router.get("/workout-plans")
def list_plans(
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=200),
    has_advanced_technique: Optional[bool] = Query(default=None),
    identity: Identity = Depends(get_current_identity),
):
    return {
        "items": services.list_workout_plans(
            status,
            limit,
            user_id=identity.user_id,
            has_advanced_technique=has_advanced_technique,
        )
    }


@router.get("/workout-plans/{plan_id}")
def get_plan(
    plan_id: int,
    session_id: Optional[int] = Query(default=None),
    identity: Identity = Depends(get_current_identity),
):
    """Ficha do usuário. Fix M97 (2026-10-26): com `session_id` (Modo Treino),
    a ficha sai como ESSA sessão a executa - numa sessão de deload, cada item
    com a prescrição de deload desde a 1ª série e sem técnica (dono
    `domain/deload.py`, via `deload_periods.apply_session_deload_to_plan`).
    Sessão normal: a mesma resposta de sem `session_id`."""
    plan = services.get_workout_plan(plan_id, user_id=identity.user_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plano de treino não encontrado.")
    if session_id is None:
        return plan
    with transaction() as conn:
        return deload_periods.apply_session_deload_to_plan(conn, plan, identity.user_id, session_id)


@router.post("/workout-plans")
def create_plan(payload: WorkoutPlanIn, identity: Identity = Depends(get_current_identity)):
    return services.create_workout_plan(payload.model_dump(), user_id=identity.user_id)


@router.put("/workout-plans/{plan_id}")
def update_plan(plan_id: int, payload: WorkoutPlanIn, identity: Identity = Depends(get_current_identity)):
    data = payload.model_dump()
    # exclude_unset nos exercícios: permite a _replace_plan_exercises
    # distinguir campo avançado NÃO enviado (preserva o valor persistido) de
    # enviado como null (limpa). Ver workout_plan_exercise_merge.py.
    # Lista de exercícios AUSENTE do corpo (ficha finalizada: a tela só salva
    # o cabeçalho) = não tocar nos exercícios; `[]` explícito continua
    # significando "ficha sem exercícios".
    data["exercises"] = (
        [ex.model_dump(exclude_unset=True) for ex in payload.exercises]
        if "exercises" in payload.model_fields_set
        else None
    )
    try:
        result = services.update_workout_plan(plan_id, data, user_id=identity.user_id)
    except workout_plan_edit_rules.PlanEditError as exc:
        # 409 = ficha finalizada; 422 = mudança do grupamento muscular.
        raise HTTPException(status_code=exc.status, detail={"reason": exc.reason, "message": exc.message})
    if not result:
        raise HTTPException(status_code=404, detail="Plano de treino não encontrado.")
    return result


@router.post("/workout-plans/{plan_id}/refresh-prescription")
def refresh_plan_prescription(plan_id: int, identity: Identity = Depends(get_current_identity)):
    """Recalcula e recongela a prescricao da ficha (PLANO_20260912.md 6.3).

    Rota da api-core (catch-all): sem Terraform de rota.

    So vale para ficha com `status='planned'` e `plan_date >= hoje`. Fora
    disso a resposta e **409 Conflict**, com `detail.reason` legivel: o corpo
    do pedido esta correto (nao ha o que validar, ele nem tem corpo) - o que
    impede a operacao e o estado atual da ficha. 422 diria "voce mandou algo
    errado", que nao e o caso."""
    try:
        return services.refresh_plan_prescription(plan_id, user_id=identity.user_id)
    except services.PlanNotFoundError:
        raise HTTPException(status_code=404, detail="Plano de treino não encontrado.")
    except services.PlanPrescriptionRefreshError as exc:
        raise HTTPException(status_code=409, detail={"reason": exc.reason, "message": exc.message})


@router.get("/workout-plans/{plan_id}/exercise-swap-options")
def list_plan_exercise_swap_options(plan_id: int, identity: Identity = Depends(get_current_identity)):
    """Exercicios elegiveis para a troca em cada linha salva da ficha nao
    finalizada (mesma regra do 422 `muscle_group_locked`; dono:
    `workout_plan_edit_rules`). Rota da api-core (catch-all): sem Terraform."""
    try:
        return services.list_exercise_swap_options(plan_id, user_id=identity.user_id)
    except services.PlanNotFoundError:
        raise HTTPException(status_code=404, detail="Plano de treino não encontrado.")


@router.get("/workout-plans/{plan_id}/exercises/{exercise_id}/equipment-options")
def list_plan_exercise_equipment_options(
    plan_id: int, exercise_id: int, identity: Identity = Depends(get_current_identity)
):
    """M8: equipamentos da academia da ficha que servem o exercicio da linha,
    com elegibilidade D13a. Rota da api-core (catch-all): sem Terraform."""
    try:
        return workout_plan_equipment.list_equipment_options(plan_id, exercise_id, identity.user_id)
    except services.PlanNotFoundError:
        raise HTTPException(status_code=404, detail="Plano de treino ou exercício não encontrado.")
    except workout_plan_equipment.PlanExerciseSubstitutionError as exc:
        raise HTTPException(status_code=exc.status, detail={"reason": exc.reason, "message": exc.message})


@router.post("/workout-plans/{plan_id}/exercises/{exercise_id}/substitute-equipment")
def substitute_plan_exercise_equipment(
    plan_id: int,
    exercise_id: int,
    payload: PlanExerciseEquipmentSubstituteIn,
    identity: Identity = Depends(get_current_identity),
):
    """M8: troca o equipamento de UMA linha da ficha e recongela a prescricao
    dela pelo mesmo caminho do `refresh-prescription`. 409 = estado da ficha
    impede (nao planejada, data passada, sem academia, exercicio sem vinculo,
    mesmo equipamento); 422 = o equipamento pedido nao serve (fora da
    academia, par incoerente, D13a). Rota da api-core (catch-all)."""
    try:
        return workout_plan_equipment.substitute_equipment(
            plan_id, exercise_id, payload.gym_equipment_id, identity.user_id
        )
    except services.PlanNotFoundError:
        raise HTTPException(status_code=404, detail="Plano de treino ou exercício não encontrado.")
    except services.PlanPrescriptionRefreshError as exc:
        raise HTTPException(status_code=409, detail={"reason": exc.reason, "message": exc.message})
    except workout_plan_equipment.PlanExerciseSubstitutionError as exc:
        raise HTTPException(status_code=exc.status, detail={"reason": exc.reason, "message": exc.message})


@router.delete("/workout-plans/{plan_id}")
def delete_plan(plan_id: int, identity: Identity = Depends(get_current_identity)):
    deleted = services.delete_workout_plan(plan_id, user_id=identity.user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Plano de treino não encontrado.")
    return {"status": "ok", "plan_id": plan_id}


@router.get("/workout-plans/{plan_id}/cardio")
def get_plan_slot_cardio(plan_id: int, identity: Identity = Depends(get_current_identity)):
    """Cardio prescrito no slot do Modelo desta ficha, pronto para exibir
    (M98 C3b): o Modo Treino mostra ao encerrar a força. Etapas, textos, tempo
    total e faixas de bpm vêm prontos (`cardio_display`); sem cardio, `items`
    vazio. Rota da api-core (catch-all): sem Terraform."""
    from .cardio_display import slot_cardio_for_plan

    plan = services.get_workout_plan(plan_id, user_id=identity.user_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plano de treino não encontrado.")
    return slot_cardio_for_plan(plan, identity.user_id)


@router.get("/workout-plans/{plan_id}/muscle-map.png")
def get_plan_muscle_map(plan_id: int, identity: Identity = Depends(get_current_identity)):
    """PNG (frente + costas) com os grupos musculares da ficha destacados —
    mapa corporal v1 (`app/body_map_render.py`, a mesma arte e regiões do
    rodapé do card ilustrado e da web), exibido na página da ficha e pensado
    para o app do relógio Zepp."""
    plan = services.get_workout_plan(plan_id, user_id=identity.user_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plano de treino não encontrado.")

    slug_intensity = services.resolve_plan_muscle_slugs(plan.get("exercises") or [])
    png_bytes = render_muscle_map_png(slug_intensity)
    return Response(content=png_bytes, media_type="image/png")


@router.post("/workout-plans/generate", status_code=202)
def generate_plan(payload: WorkoutPlanGenerateIn, identity: Identity = Depends(get_current_identity)):
    """Assincrono desde a Fase 3 da migracao AWS (202 + job_id + polling via
    GET /jobs/{job_id}) - a geracao em si (`plan_generation_core.dispatch_plan_generation`,
    mesma logica exata do endpoint sincrono anterior) roda na Lambda
    `musclefit-plan-generator`, invocada aqui de forma assincrona
    (`InvocationType=Event`). Motivo: a chamada a OpenAI pode levar varios
    segundos, incompativel com o timeout do API Gateway - ver
    docs/aws-migration-plan.md, secao 1.2.

    **PR-18:** a ficha so e gerada a partir de um Modelo de Treino. A
    validacao roda AQUI, no request sincrono, antes de criar o job:
    `template_id` ausente ja e 422 do proprio schema
    (`WorkoutPlanGenerateIn`), e modelo inexistente/nao atribuido ou slot
    invalido viram 404/422 imediatos (`resolve_template_and_slot`). Sem
    isso, o erro so apareceria segundos depois como um job `failed`, sem
    status HTTP - experiencia ruim e dificil de tratar no frontend."""
    resolve_template_and_slot(payload.template_id, identity.user_id, payload.template_slot_sequence)

    job_id = async_jobs.create_job(
        "plan-generator",
        request={"payload": payload.model_dump(mode="json"), "user_id": identity.user_id},
        owner_user_id=identity.user_id,
    )
    async_jobs.invoke_async(
        "PLAN_GENERATOR_FUNCTION_NAME",
        {"job_id": job_id, "payload": payload.model_dump(mode="json"), "user_id": identity.user_id},
    )
    return JSONResponse(status_code=202, content={"job_id": job_id, "status": "pending"})
