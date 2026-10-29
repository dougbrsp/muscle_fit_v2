from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from .db import DataApiError, get_conn
from .models import BodyCompositionIn
from .request_models import BodyCompositionUpdateIn
from .security import Identity, get_current_identity
from .services import upsert_body_composition

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/body-composition", tags=["body-composition"])


def _connect():
    try:
        return get_conn()
    except DataApiError:
        logger.exception("Banco indisponivel em %s", __name__)
        raise HTTPException(
            status_code=503,
            detail={"error": "database_unavailable", "message": "Banco de dados indisponível."},
        )


@router.post("")
def create_or_update_body_composition(
    payload: BodyCompositionIn,
    identity: Identity = Depends(get_current_identity),
):
    """Upsert por (user_id, local_date) - contrato legado usado pelo
    MCP/OpenClaw, unificado aqui junto com o CRUD GET/PUT/DELETE."""
    return upsert_body_composition(payload.model_dump(), user_id=identity.user_id)


@router.get("")
def list_body_composition(
    limit: int = 200,
    identity: Identity = Depends(get_current_identity),
):
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, local_date, measured_at,
                       weight_kg, bmi, body_fat_percent, fat_mass_kg, fat_free_mass_kg,
                       muscle_mass_kg, skeletal_muscle_percent, body_water_percent,
                       visceral_fat_rating, bone_mass_kg, bmr_kcal,
                       subcutaneous_fat_percent, protein_percent, metabolic_age,
                       source, notes, created_at
                FROM body_composition
                WHERE user_id = %s
                ORDER BY local_date DESC, id DESC
                LIMIT %s
                """,
                (identity.user_id, min(limit, 500)),
            )
            rows = cur.fetchall()
        return {"items": rows}
    finally:
        conn.close()


@router.get("/{entry_id}")
def get_body_composition(
    entry_id: int,
    identity: Identity = Depends(get_current_identity),
):
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM body_composition WHERE id=%s AND user_id=%s", (entry_id, identity.user_id))
            row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Registro não encontrado.")
        return row
    finally:
        conn.close()


@router.put("/{entry_id}")
def update_body_composition(
    entry_id: int,
    body: BodyCompositionUpdateIn,
    identity: Identity = Depends(get_current_identity),
):
    # `exclude_unset`: campo enviado como null grava NULL; campo ausente nao muda.
    payload = body.model_dump(exclude_unset=True)
    allowed = {
        "local_date", "measured_at", "weight_kg", "bmi",
        "body_fat_percent", "fat_mass_kg", "fat_free_mass_kg",
        "muscle_mass_kg", "skeletal_muscle_percent", "body_water_percent",
        "visceral_fat_rating", "bone_mass_kg", "bmr_kcal",
        "subcutaneous_fat_percent", "protein_percent", "metabolic_age",
        "source", "notes",
    }
    updates = {k: v for k, v in payload.items() if k in allowed}
    if not updates:
        raise HTTPException(status_code=400, detail="Nenhum campo válido para atualizar.")

    set_clause = ", ".join(f"{col}=%s" for col in updates)
    values = list(updates.values()) + [entry_id, identity.user_id]

    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE body_composition SET {set_clause} WHERE id=%s AND user_id=%s", values)
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Registro não encontrado.")
        conn.commit()
        return {"status": "ok", "id": entry_id}
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        logger.exception("Falha interna em %s", __name__)
        raise HTTPException(status_code=500, detail="Erro interno ao processar a solicitação.")
    finally:
        conn.close()


@router.delete("/{entry_id}")
def delete_body_composition(
    entry_id: int,
    identity: Identity = Depends(get_current_identity),
):
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM body_composition WHERE id=%s AND user_id=%s", (entry_id, identity.user_id))
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Registro não encontrado.")
        conn.commit()
        return {"status": "deleted", "id": entry_id}
    except HTTPException:
        conn.rollback()
        raise
    except Exception:
        conn.rollback()
        logger.exception("Falha interna em %s", __name__)
        raise HTTPException(status_code=500, detail="Erro interno ao processar a solicitação.")
    finally:
        conn.close()
