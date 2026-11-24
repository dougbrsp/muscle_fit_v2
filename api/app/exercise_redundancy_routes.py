"""Prévia de redundância do editor manual de Modelo (issue #288, fatia 2).

    POST /workout-plan-templates/redundancy-preview   modelos.treino/editar (a mesma do editor)

Só orquestra: o nível sai de `domain/exercise_redundancy` (dono), a contribuição muscular de
`intelligence/muscle_contribution_resolver` (reduzida a grupo -> papel, sem recalcular pesos) e o perfil de resistência
da tabela `equipment_exercise_resistance_profiles` (validado pelo dono `resistance_profile` dentro de `compare`).
`equipment_id` do item é `equipment.id` (par `equipment_exercises` = equipment_id + exercise_id). Em lote: 1 leitura do
catálogo, 2 do resolvedor, 1 de perfis, qualquer que seja o número de itens. Sem a migração 126, perfis = None (sem 503).
"""

from __future__ import annotations

import json
from itertools import combinations
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException

from .access import require_permission
from .db import get_conn
from .db_helpers import connect_or_503
from .domain import exercise_redundancy as red
from .intelligence.muscle_contribution_resolver import resolve_muscle_contributions_with_cursor
from .request_models import RedundancyPreviewIn
from .resistance_profiles import PROFILES

router = APIRouter(prefix="/workout-plan-templates", tags=["workout-plan-templates"])

_RESISTANCE_COLUMNS = ("source", "mechanism", "trajectory", "profile", "line", "direction_changes", "confidence",
                       "status", "origin")
_TABLE_SQL = "SELECT COUNT(*) AS n FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s"
_EXERCISES_SQL = (
    "SELECT id, biomechanical_pattern, primary_joint, movement, body_position, trajectory, is_unilateral "
    "FROM exercise_catalog WHERE id IN ({ids})"
)
_PROFILES_SQL = (
    "SELECT eqex.equipment_id, eqex.exercise_id, p.flags_json, "
    + ", ".join(f"p.{c}" for c in _RESISTANCE_COLUMNS)
    + f" FROM equipment_exercises eqex JOIN {PROFILES} p ON p.equipment_exercise_id = eqex.id WHERE {{pairs}}"
)


def _flags(raw: Any) -> list[str]:
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8")
    try:
        bruto = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return []
    return list(bruto) if isinstance(bruto, list) else []


def _groups(rows: list[dict[str, Any]]) -> dict[int, str]:
    """grupo -> papel a partir do que o resolvedor emitiu; 'Outros' (sem grupo) não é grupo; primário vence."""
    out: dict[int, str] = {}
    for row in rows:
        gid = row.get("muscle_group_id")
        if gid is None:
            continue
        role = str(row.get("role") or "").lower()
        if out.get(gid) != red.ROLE_PRIMARY:
            out[gid] = role
    return out


def _profiles(cur: Any, pairs: list[tuple[int, int]]) -> dict[tuple[int, int], dict[str, Any]]:
    if not pairs:
        return {}
    cur.execute(_TABLE_SQL, (PROFILES,))
    if not int((cur.fetchone() or {}).get("n") or 0):
        return {}
    params = [v for pair in pairs for v in pair]
    where = " OR ".join(["(eqex.equipment_id = %s AND eqex.exercise_id = %s)"] * len(pairs))
    cur.execute(_PROFILES_SQL.format(pairs=where), tuple(params))
    out: dict[tuple[int, int], dict[str, Any]] = {}
    for r in cur.fetchall() or []:
        out[(int(r["equipment_id"]), int(r["exercise_id"]))] = {
            **{c: r.get(c) for c in _RESISTANCE_COLUMNS}, "flags": _flags(r.get("flags_json"))
        }
    return out


@router.post(
    "/redundancy-preview",
    dependencies=[Depends(require_permission("modelos.treino", "editar", min_scope="alunos"))],
)
def redundancy_preview(payload: RedundancyPreviewIn) -> dict[str, Any]:
    if payload.min_level is not None and payload.min_level not in red.LEVEL_ORDER:
        raise HTTPException(status_code=422, detail={"error": "invalid_min_level", "field": "min_level"})
    items = payload.items
    exercise_ids = sorted({int(i.mapping_id) for i in items})
    pairs = sorted({(int(i.equipment_id), int(i.mapping_id)) for i in items if i.equipment_id})
    with connect_or_503(get_conn) as conn:
        with conn.cursor() as cur:
            cur.execute(_EXERCISES_SQL.format(ids=",".join(["%s"] * len(exercise_ids))), tuple(exercise_ids))
            catalog = {int(r["id"]): r for r in cur.fetchall() or []}
            contributions = resolve_muscle_contributions_with_cursor(cur, set(exercise_ids))
            profiles = _profiles(cur, pairs)

    candidates: list[Optional[red.Candidate]] = []
    for i in items:
        ex = catalog.get(int(i.mapping_id))
        if ex is None:
            candidates.append(None)  # exercício fora do catálogo: sem dado, sem par
            continue
        eq = int(i.equipment_id) if i.equipment_id else None
        candidates.append(red.Candidate(
            exercise_id=int(i.mapping_id),
            equipment_id=eq,
            contributions=_groups(contributions.get(int(i.mapping_id), [])),
            biomechanical_pattern=ex.get("biomechanical_pattern"),
            primary_joint=ex.get("primary_joint"),
            movement=ex.get("movement"),
            body_position=ex.get("body_position"),
            trajectory=ex.get("trajectory"),
            unilateral=None if ex.get("is_unilateral") is None else bool(ex["is_unilateral"]),
            resistance=profiles.get((eq, int(i.mapping_id))) if eq else None,
        ))

    target = payload.target_index
    indexes = (
        [(min(target, j), max(target, j)) for j in range(len(items)) if j != target]
        if target is not None
        else list(combinations(range(len(items)), 2))
    )
    floor = payload.min_level or red.DISPLAY_MIN_LEVEL
    out: list[dict[str, Any]] = []
    for a, b in indexes:
        if candidates[a] is None or candidates[b] is None:
            continue
        result = red.compare(candidates[a], candidates[b])
        if not red.at_least(result.level, floor):
            continue
        out.append({
            "a_index": a, "b_index": b,
            "a_mapping_id": int(items[a].mapping_id), "b_mapping_id": int(items[b].mapping_id),
            **red.to_dict(result),
        })
    out.sort(key=lambda r: (-red.level_rank(r["level"]), r["a_index"], r["b_index"]))
    return {"items": out}
