from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query

from . import services
from .intelligence.volume_comparison import PERIOD_GRANULARITIES
from .request_models import MuscleGroupBodyMapIn
from .intelligence import BodyCompositionTrendService, MuscleRecoveryService, VolumeService
from .access import require_permission
from .security import Identity, get_current_identity

router = APIRouter(prefix="/analytics", tags=["analytics", "dashboard-modules"], dependencies=[Depends(get_current_identity)])

_GRANULARITY_PATTERN = f"^({'|'.join(PERIOD_GRANULARITIES)})$"


@router.get("/body-composition-trend")
def body_composition_trend(
    days: int = Query(default=90, ge=1, le=3650),
    identity: Identity = Depends(require_permission("corpo.indicadores", "ver")),
):
    service = BodyCompositionTrendService()
    return service.get_body_trend(days=days, user_id=identity.user_id)


@router.get("/training-volume")
def training_volume(
    days: int = Query(default=30, ge=1, le=3650),
    identity: Identity = Depends(require_permission("volume.grupamentos", "ver")),
    granularity: Annotated[Optional[str], Query(pattern=_GRANULARITY_PATTERN)] = None,
):
    """`granularity` (opcional, `d7`|`week`|`month`): acrescenta
    `period_comparison`, a aba de tendencia atual x anterior pronta (dono:
    `slot_session_comparison.build_period_comparison`), com as janelas no
    "hoje" local do usuario. A janela anterior precisa caber em `days`."""
    from .intelligence.methodology_volume_service import annotate_group_hierarchy, load_group_hierarchy
    from .intelligence.slot_session_comparison import build_period_comparison

    service = VolumeService()
    overview = service.get_volume_breakdown(days=days, user_id=identity.user_id)
    trend = service.get_volume_trend(days=min(max(days, 7), 180), user_id=identity.user_id)
    # Grupo pai e macro-grupo por linha da serie (dono: `methodology_volume`),
    # para /volume-grupamentos agrupar. O overview nao ganha os campos: status
    # e metas sao por grupo e nao se agregam.
    annotate_group_hierarchy(trend["items"], load_group_hierarchy())
    body = {"overview": overview, "trend": trend}
    if granularity is not None:
        body["period_comparison"] = build_period_comparison(
            trend["items"], granularity, service._user_today(identity.user_id)
        )
    return body


@router.get("/training-volume-by-split-day")
def training_volume_by_split_day(
    days: int = Query(default=180, ge=1, le=3650),
    identity: Identity = Depends(require_permission("volume.grupamentos", "ver")),
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
    identity: Identity = Depends(require_permission("dashboards", "ver")),
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
    identity: Identity = Depends(require_permission("dashboards", "ver")),
):
    service = MuscleRecoveryService()
    return {
        "days": days,
        "items": service.get_consecutive_day_recovery_checks(days=days, user_id=identity.user_id),
    }


@router.get("/muscle-group-body-map", dependencies=[Depends(require_permission("volume.grupamentos", "ver"))])
def muscle_group_body_map():
    return {"items": services.list_muscle_group_body_map()}


@router.put("/muscle-group-body-map", dependencies=[Depends(require_permission("admin.catalogos", "editar"))])
def upsert_muscle_group_body_map(
    payload: MuscleGroupBodyMapIn,
):
    muscle_group = payload.muscle_group.strip()
    slugs = payload.slugs
    if not muscle_group:
        return {"error": "muscle_group e slugs (lista) são obrigatórios"}

    return services.upsert_muscle_group_body_map(muscle_group, [str(slug) for slug in slugs])
