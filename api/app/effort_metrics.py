"""`GET /effort-metrics` — EPOC/PAI de 7 dias ESTIMADOS por dia (M99).

Substitui o CRUD manual `/manual-effort-metrics` (a tabela
`manual_effort_metrics` fica como histórico, sem escrita). O valor vem pronto
do motor `domain/cardio_load.py` via `effort_estimates`.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from . import effort_estimates
from .db import get_conn
from .db_helpers import connect_or_503
from .intelligence.dashboard.time_windows import user_today_for
from .security import Identity, get_current_identity

router = APIRouter(prefix="/effort-metrics", tags=["effort-metrics"])

logger = logging.getLogger(__name__)

DEFAULT_DAYS = 60
MAX_DAYS = 365


def _connect():
    return connect_or_503(get_conn)


@router.get("")
def list_effort_metrics(
    days: int = Query(default=DEFAULT_DAYS, ge=1, le=MAX_DAYS),
    identity: Identity = Depends(get_current_identity),
):
    """Dias com estimativa nos últimos `days` dias (hoje no fuso do usuário),
    do mais recente para o mais antigo."""
    conn = _connect()
    try:
        today = user_today_for(conn, identity.user_id)
        with conn.cursor() as cur:
            items = effort_estimates.load_recent(effort_estimates.cursor_fetch(cur), identity.user_id, today, days)
        return {"items": items}
    except HTTPException:
        raise
    except Exception:
        # SEG-12: o texto da exceção vai para o log, nunca para o corpo.
        logger.exception("Falha interna em %s", __name__)
        raise HTTPException(status_code=500, detail="Erro interno ao processar a solicitação.")
    finally:
        conn.close()
