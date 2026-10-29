from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from .intelligence import CardioAgentContextService, DashboardIntelligenceLayer
from .security import Identity, get_current_identity

router = APIRouter(prefix="/analytics", tags=["analytics", "dashboard-intelligence"], dependencies=[Depends(get_current_identity)])


@router.get("/training-evolution")
def training_evolution(
    days: int = Query(default=90, ge=1, le=3650),
    identity: Identity = Depends(get_current_identity),
):
    layer = DashboardIntelligenceLayer()
    return layer.get_training_evolution(days=days, user_id=identity.user_id)


@router.get("/cardio-agent-context")
def cardio_agent_context(
    days: int = Query(default=14, ge=1, le=90),
    identity: Identity = Depends(get_current_identity),
):
    # Multiusuario Parte 4: Identity agora disponivel - propaga user_id para
    # build_context() (fecha a ressalva documentada na Parte 2).
    service = CardioAgentContextService()
    return service.build_context(days=days, user_id=identity.user_id)
