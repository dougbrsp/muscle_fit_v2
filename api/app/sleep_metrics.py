from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from .db import get_conn
from .db_helpers import connect_or_503
from .request_models import SleepMetricsUpdateIn
from .security import Identity, get_current_identity

router = APIRouter(prefix="/sleep-metrics", tags=["sleep-metrics"])


logger = logging.getLogger(__name__)


def _connect():
    return connect_or_503(get_conn)


_ALLOWED_FIELDS = {
    "local_date",
    "duration_hours",
    "regularity_percent",
    "deep_sleep_hours",
    "rem_sleep_hours",
    "awake_count",
    "awake_hours",
    "notes",
}


@router.post("")
def create_or_update_sleep_metrics(
    payload: Dict[str, Any],
    identity: Identity = Depends(get_current_identity),
):
    if not payload.get("local_date"):
        raise HTTPException(status_code=400, detail="local_date é obrigatório.")

    fields = {k: v for k, v in payload.items() if k in _ALLOWED_FIELDS}
    fields["user_id"] = identity.user_id

    columns = list(fields.keys())
    values = list(fields.values())
    placeholders = ", ".join(["%s"] * len(columns))
    update_clause = ", ".join(
        f"{col}=VALUES({col})" for col in columns if col not in ("local_date", "user_id")
    )

    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO sleep_metrics ({', '.join(columns)})
                VALUES ({placeholders})
                ON DUPLICATE KEY UPDATE {update_clause}
                """,
                values,
            )
            conn.commit()
            new_id = cur.lastrowid
            cur.execute(
                "SELECT id FROM sleep_metrics WHERE local_date=%s AND user_id=%s",
                (fields["local_date"], identity.user_id),
            )
            row = cur.fetchone()
        return {"status": "ok", "id": row["id"] if row else new_id}
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        # SEG-12: o texto da excecao vai para o log, nunca para o corpo.
        logger.exception("Falha interna em %s", __name__)
        raise HTTPException(status_code=500, detail="Erro interno ao processar a solicitação.")
    finally:
        conn.close()


@router.get("")
def list_sleep_metrics(
    days: Optional[int] = Query(default=None, ge=1, le=3650),
    limit: int = Query(default=200, ge=1, le=1000),
    identity: Identity = Depends(get_current_identity),
):
    conn = _connect()
    try:
        with conn.cursor() as cur:
            if days:
                cur.execute(
                    """
                    SELECT id, local_date, duration_hours, regularity_percent,
                           deep_sleep_hours, rem_sleep_hours, awake_count, awake_hours,
                           notes, created_at, updated_at
                    FROM sleep_metrics
                    WHERE user_id = %s AND local_date >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
                    ORDER BY local_date DESC, id DESC
                    LIMIT %s
                    """,
                    (identity.user_id, days, min(limit, 1000)),
                )
            else:
                cur.execute(
                    """
                    SELECT id, local_date, duration_hours, regularity_percent,
                           deep_sleep_hours, rem_sleep_hours, awake_count, awake_hours,
                           notes, created_at, updated_at
                    FROM sleep_metrics
                    WHERE user_id = %s
                    ORDER BY local_date DESC, id DESC
                    LIMIT %s
                    """,
                    (identity.user_id, min(limit, 1000)),
                )
            rows = cur.fetchall()
        return {"items": rows}
    finally:
        conn.close()


@router.get("/{entry_id}")
def get_sleep_metrics(
    entry_id: int,
    identity: Identity = Depends(get_current_identity),
):
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM sleep_metrics WHERE id=%s AND user_id=%s", (entry_id, identity.user_id))
            row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Registro não encontrado.")
        return row
    finally:
        conn.close()


@router.put("/{entry_id}")
def update_sleep_metrics(
    entry_id: int,
    body: SleepMetricsUpdateIn,
    identity: Identity = Depends(get_current_identity),
):
    payload = body.model_dump(exclude_unset=True)
    updates = {k: v for k, v in payload.items() if k in _ALLOWED_FIELDS}
    if not updates:
        raise HTTPException(status_code=400, detail="Nenhum campo válido para atualizar.")

    set_clause = ", ".join(f"{col}=%s" for col in updates)
    values = list(updates.values()) + [entry_id, identity.user_id]

    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE sleep_metrics SET {set_clause} WHERE id=%s AND user_id=%s", values)
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Registro não encontrado.")
        conn.commit()
        return {"status": "ok", "id": entry_id}
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        # SEG-12: o texto da excecao vai para o log, nunca para o corpo.
        logger.exception("Falha interna em %s", __name__)
        raise HTTPException(status_code=500, detail="Erro interno ao processar a solicitação.")
    finally:
        conn.close()


@router.delete("/{entry_id}")
def delete_sleep_metrics(
    entry_id: int,
    identity: Identity = Depends(get_current_identity),
):
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM sleep_metrics WHERE id=%s AND user_id=%s", (entry_id, identity.user_id))
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Registro não encontrado.")
        conn.commit()
        return {"status": "deleted", "id": entry_id}
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        # SEG-12: o texto da excecao vai para o log, nunca para o corpo.
        logger.exception("Falha interna em %s", __name__)
        raise HTTPException(status_code=500, detail="Erro interno ao processar a solicitação.")
    finally:
        conn.close()
