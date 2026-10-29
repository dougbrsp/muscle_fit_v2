from __future__ import annotations


from fastapi import APIRouter, Depends, Query

from . import services
from .request_models import MuscleGroupBodyMapIn
from .intelligence import BodyCompositionTrendService, MuscleRecoveryService, VolumeService
from .security import Identity, get_current_identity, require_admin

router = APIRouter(prefix="/analytics", tags=["analytics", "dashboard-modules"], dependencies=[Depends(get_current_identity)])


@router.get("/body-composition-trend")
def body_composition_trend(
    days: int = Query(default=90, ge=1, le=3650),
    identity: Identity = Depends(get_current_identity),
):
    service = BodyCompositionTrendService()
    return service.get_body_trend(days=days, user_id=identity.user_id)


@router.get("/training-volume")
def training_volume(
    days: int = Query(default=30, ge=1, le=3650),
    identity: Identity = Depends(get_current_identity),
):
    from .intelligence.methodology_volume_service import annotate_group_hierarchy, load_group_hierarchy

    service = VolumeService()
    overview = service.get_volume_breakdown(days=days, user_id=identity.user_id)
    trend = service.get_volume_trend(days=min(max(days, 7), 180), user_id=identity.user_id)
    # Grupo pai e macro-grupo por linha da serie (dono: `methodology_volume`),
    # para /volume-grupamentos agrupar. O overview nao ganha os campos: status
    # e metas sao por grupo e nao se agregam.
    annotate_group_hierarchy(trend["items"], load_group_hierarchy())
    return {"overview": overview, "trend": trend}


@router.get("/training-volume-by-split-day")
def training_volume_by_split_day(
    days: int = Query(default=180, ge=1, le=3650),
    identity: Identity = Depends(get_current_identity),
):
    service = VolumeService()
    clamped_days = min(max(days, 7), 180)
    return {
        "days": clamped_days,
        "items": service.get_volume_by_split_day(days=clamped_days, user_id=identity.user_id),
    }


@router.get("/muscle-recovery")
def muscle_recovery(
    days: int = Query(default=14, ge=1, le=3650),
    identity: Identity = Depends(get_current_identity),
):
    # Multiusuario Parte 4: Identity agora disponivel - propaga user_id para
    # o fator global de TSB (fecha a ressalva documentada na Parte 2, ver
    # IntelligenceServiceBase.get_latest_intervals_wellness).
    service = MuscleRecoveryService()
    return {
        "days": days,
        "items": service.get_recovery_overview(days=days, user_id=identity.user_id),
        "global_readiness_adjustment": service.get_global_readiness_adjustment(user_id=identity.user_id),
    }


@router.get("/muscle-recovery-consecutive-check")
def muscle_recovery_consecutive_check(
    days: int = Query(default=7, ge=1, le=3650),
    identity: Identity = Depends(get_current_identity),
):
    service = MuscleRecoveryService()
    return {
        "days": days,
        "items": service.get_consecutive_day_recovery_checks(days=days, user_id=identity.user_id),
    }


@router.get("/muscle-group-body-map")
def muscle_group_body_map():
    return {"items": services.list_muscle_group_body_map()}


@router.put("/muscle-group-body-map", dependencies=[Depends(require_admin)])
def upsert_muscle_group_body_map(
    payload: MuscleGroupBodyMapIn,
):
    muscle_group = payload.muscle_group.strip()
    slugs = payload.slugs
    if not muscle_group:
        return {"error": "muscle_group e slugs (lista) são obrigatórios"}

    return services.upsert_muscle_group_body_map(muscle_group, [str(slug) for slug in slugs])
