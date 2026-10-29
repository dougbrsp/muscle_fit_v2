"""Rotas de leitura do estado do Motor de Progressao v2 (PR-10;
PLANO_20260912.md secoes 9 e 15, linha do PR 10).

    GET /progression/tracks?exercise_id=&equipment_id=&goal=   -> trilhas do usuario corrente
    GET /progression/tracks/{track_id}/decisions          -> decisoes daquela trilha e do grupo dela

Read-only: nenhuma escrita, nenhum DDL. Autorizacao = usuario corrente,
SEMPRE: o `user_id` vem da identidade resolvida pelo token/sessao e nao existe
em path, query nem corpo. Trilha de outro usuario responde 404 (nao 403: nao
vaza a existencia do id).

Paths top-level -> catch-all da api-core; nada de Terraform de rota.

Tabelas ausentes (codigo publicado antes da migracao 036) -> 503 explicito,
nunca 500 generico.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from .date_helpers import isoformat_or_value
from .db import transaction
from .domain.prior_fatigue import band_label
from .domain.training_goal import TRAINING_GOAL_COLUMN, sql_goal_is, sql_goal_join
from .progression_state import (
    DECISIONS_TABLE,
    GROUP_STATES_TABLE,
    TRACKS_TABLE,
    engine_mode,
)
from .security import Identity, get_current_identity
from .training_goal_query import goal_query

router = APIRouter(prefix="/progression", tags=["progression"])

MAX_DECISIONS = 200

_MISSING_TABLE_MARKERS = ("1146", "doesn't exist", "does not exist", "unknown table")


def is_missing_progression_table_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _MISSING_TABLE_MARKERS) and "progression_" in text


logger = logging.getLogger(__name__)


def _unavailable(exc: Exception) -> HTTPException:
    # SEG-12: o texto da excecao vai para o log, nunca para o corpo do 503.
    logger.error("Tabela indisponivel em %s; respondendo 503", __name__, exc_info=exc)
    return HTTPException(
        status_code=503,
        detail={
            "error": "progression_state_unavailable",
            "message": "Estado de progressão indisponível: migração 036 pendente neste ambiente.",
        },
    )


_iso = isoformat_or_value  # COD-06: corpo em `app/date_helpers.py`


def _track_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "exercise_id": int(row["exercise_id"]),
        "equipment_id": int(row["equipment_id"]),
        # contexto de fadiga previa da trilha (migracao 068) e o rotulo do dono
        "fatigue_context": row.get("fatigue_context"),
        "fatigue_context_label": band_label(row.get("fatigue_context")),
        # M97 F1: objetivo de treino da trilha (aditivo)
        "training_goal": row.get(TRAINING_GOAL_COLUMN),
        "exercise_name": row.get("exercise_name"),
        "equipment_name": row.get("equipment_name"),
        "muscle_group_id": None if row.get("muscle_group_id") is None else int(row["muscle_group_id"]),
        "muscle_group_name": row.get("muscle_group_name"),
        "load_status": row.get("load_status"),
        "working_load_kg": float(row["working_load_kg"]) if row.get("working_load_kg") is not None else None,
        "sets_count": int(row["sets_count"]),
        "technique_code": row.get("technique_code"),
        "last_executed_at": _iso(row.get("last_executed_at")),
        "last_load_change_at": _iso(row.get("last_load_change_at")),
        "last_sets_change_at": _iso(row.get("last_sets_change_at")),
        "engine_version": row.get("track_engine_version"),
        "group_state": None
        if row.get("group_state_id") is None
        else {
            "id": int(row["group_state_id"]),
            "phase": row.get("phase"),
            "phase_status": row.get("phase_status"),
            "reps_min": None if row.get("reps_min") is None else int(row["reps_min"]),
            "reps_max": None if row.get("reps_max") is None else int(row["reps_max"]),
            "pending_action": row.get("pending_action"),
            "phase_entered_at": _iso(row.get("phase_entered_at")),
            "next_eligible_progression_at": _iso(row.get("next_eligible_progression_at")),
        },
    }


_TRACKS_SQL = f"""
SELECT t.id, t.exercise_id, t.equipment_id, t.fatigue_context, t.training_goal, t.muscle_group_id, t.load_status,
       t.working_load_kg,
       t.sets_count, t.technique_code, t.last_executed_at, t.last_load_change_at, t.last_sets_change_at,
       t.engine_version AS track_engine_version,
       ec.name AS exercise_name, eq.name AS equipment_name, mg.name AS muscle_group_name,
       g.id AS group_state_id, g.phase, g.phase_status, g.reps_min, g.reps_max, g.pending_action,
       g.phase_entered_at, g.next_eligible_progression_at
FROM {TRACKS_TABLE} t
LEFT JOIN exercise_catalog ec ON ec.id = t.exercise_id
LEFT JOIN equipment eq ON eq.id = t.equipment_id
LEFT JOIN muscle_groups mg ON mg.id = t.muscle_group_id
LEFT JOIN {GROUP_STATES_TABLE} g ON g.user_id = t.user_id AND g.muscle_group_id = t.muscle_group_id
  AND {sql_goal_join("g", "t")}
WHERE t.user_id = %s AND {sql_goal_is("t")}
"""


@router.get("/tracks")
def list_tracks(
    exercise_id: Optional[int] = Query(default=None, ge=1),
    equipment_id: Optional[int] = Query(default=None, ge=1),
    goal: str = Depends(goal_query),
    identity: Identity = Depends(get_current_identity),
) -> dict[str, Any]:
    """Trilhas do usuario corrente NO OBJETIVO `goal` (M97 F1; ausente =
    default), com a fase do grupo de cada uma (a do mesmo objetivo)."""
    sql, params = _TRACKS_SQL, [int(identity.user_id), goal]
    if exercise_id is not None:
        sql += " AND t.exercise_id = %s"
        params.append(int(exercise_id))
    if equipment_id is not None:
        sql += " AND t.equipment_id = %s"
        params.append(int(equipment_id))
    sql += " ORDER BY t.last_executed_at DESC, t.id DESC"
    try:
        with transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                rows = cur.fetchall() or []
    except Exception as exc:  # noqa: BLE001
        if is_missing_progression_table_error(exc):
            raise _unavailable(exc) from exc
        raise
    return {"items": [_track_item(row) for row in rows], "engine_mode": engine_mode()}


@router.get("/tracks/{track_id}/decisions")
def list_track_decisions(
    track_id: int,
    limit: int = Query(default=50, ge=1, le=MAX_DECISIONS),
    identity: Identity = Depends(get_current_identity),
) -> dict[str, Any]:
    """Decisoes gravadas para a trilha e para o grupo dela, mais recentes
    primeiro. Trilha de outro usuario -> 404."""
    user_id = int(identity.user_id)
    try:
        with transaction() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT t.id, t.muscle_group_id, g.id AS group_state_id FROM {TRACKS_TABLE} t "
                    f"LEFT JOIN {GROUP_STATES_TABLE} g ON g.user_id = t.user_id AND g.muscle_group_id = t.muscle_group_id "
                    f"AND {sql_goal_join('g', 't')} "
                    "WHERE t.id = %s AND t.user_id = %s",
                    (int(track_id), user_id),
                )
                track = cur.fetchone()
                if not track:
                    raise HTTPException(status_code=404, detail={"error": "progression_track_not_found"})
                cur.execute(
                    "SELECT id, group_state_id, track_id, kind, activity_id, workout_plan_exercise_id, "
                    "target_datetime, phase_before, phase_after, decision, output_json, evidence_json, "
                    f"blocked_by, engine_version, created_at FROM {DECISIONS_TABLE} "
                    "WHERE user_id = %s AND (track_id = %s OR (track_id IS NULL AND group_state_id = %s)) "
                    "ORDER BY created_at DESC, id DESC LIMIT %s",
                    (user_id, int(track_id), track.get("group_state_id"), int(limit)),
                )
                rows = cur.fetchall() or []
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        if is_missing_progression_table_error(exc):
            raise _unavailable(exc) from exc
        raise
    from .progression_state import _parse_json

    return {
        "track_id": int(track_id),
        "group_state_id": track.get("group_state_id"),
        "items": [
            {
                "id": int(row["id"]),
                "group_state_id": None if row["group_state_id"] is None else int(row["group_state_id"]),
                "track_id": None if row["track_id"] is None else int(row["track_id"]),
                "kind": row["kind"],
                "activity_id": None if row["activity_id"] is None else int(row["activity_id"]),
                "workout_plan_exercise_id": None
                if row["workout_plan_exercise_id"] is None
                else int(row["workout_plan_exercise_id"]),
                "target_datetime": _iso(row["target_datetime"]),
                "phase_before": row["phase_before"],
                "phase_after": row["phase_after"],
                "decision": row["decision"],
                "output": _parse_json(row["output_json"]),
                "evidence": _parse_json(row["evidence_json"]),
                "blocked_by": _parse_json(row["blocked_by"]) or [],
                "engine_version": row["engine_version"],
                "created_at": _iso(row["created_at"]),
            }
            for row in rows
        ],
    }
