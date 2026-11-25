"""Carregador em lote de `exercise_redundancy.Candidate` (issue #288, fatia 3).

Só lê e monta: o nível sai do dono `domain/exercise_redundancy`, a contribuição muscular do dono
`muscle_contribution_resolver` (reduzida a grupo -> papel, sem recalcular pesos) e o perfil de resistência da tabela
`equipment_exercise_resistance_profiles` (validado pelo dono `resistance_profile` dentro de `compare`).
Em lotes fixos (constantes abaixo): leituras do catálogo e de perfis proporcionais a ceil(n/lote), 2 do resolvedor. Tabela de perfis ausente (antes da migração 126): perfis = None, sem erro.
"""

from __future__ import annotations

import json
from typing import Any, Optional, Sequence

from ..domain import exercise_redundancy as red
from ..resistance_profiles import PROFILES
from .muscle_contribution_resolver import resolve_muscle_contributions_with_cursor

_RESISTANCE_COLUMNS = ("source", "mechanism", "trajectory", "profile", "line", "direction_changes", "confidence",
                       "status", "origin")
_TABLE_SQL = "SELECT COUNT(*) AS n FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s"
_EXERCISES_SQL = (
    "SELECT id, name, biomechanical_pattern, primary_joint, movement, body_position, trajectory, is_unilateral "
    "FROM exercise_catalog WHERE id IN ({ids})"
)
_PROFILES_SQL = (
    "SELECT eqex.equipment_id, eqex.exercise_id, p.flags_json, "
    + ", ".join(f"p.{c}" for c in _RESISTANCE_COLUMNS)
    + f" FROM equipment_exercises eqex JOIN {PROFILES} p ON p.equipment_exercise_id = eqex.id WHERE {{pairs}}"
)

# Operacional: 2 parâmetros por par (OR) e 1 por exercício (IN); lotes mantêm a Data API longe do teto de parâmetros.
PROFILE_QUERY_BATCH_PAIRS = 100
CATALOG_QUERY_BATCH_IDS = 500

Pair = tuple[int, Optional[int]]


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
    out: dict[tuple[int, int], dict[str, Any]] = {}
    for i in range(0, len(pairs), PROFILE_QUERY_BATCH_PAIRS):
        lote = pairs[i:i + PROFILE_QUERY_BATCH_PAIRS]
        where = " OR ".join(["(eqex.equipment_id = %s AND eqex.exercise_id = %s)"] * len(lote))
        cur.execute(_PROFILES_SQL.format(pairs=where), tuple(v for pair in lote for v in pair))
        for r in cur.fetchall() or []:
            out[(int(r["equipment_id"]), int(r["exercise_id"]))] = {
                **{c: r.get(c) for c in _RESISTANCE_COLUMNS}, "flags": _flags(r.get("flags_json"))
            }
    return out


def load_candidates(cur: Any, pairs: Sequence[Pair]) -> dict[Pair, red.Candidate]:
    """par (exercise_id, equipment_id|None) -> Candidate. Exercício fora do catálogo: par fora do resultado."""
    return load_candidates_with_names(cur, pairs)[0]


def load_candidates_with_names(
    cur: Any, pairs: Sequence[Pair]
) -> tuple[dict[Pair, red.Candidate], dict[int, str]]:
    """Como `load_candidates`, mais exercise_id -> `exercise_catalog.name` (para exibição no diagnóstico)."""
    norm = {(int(ex), int(eq) if eq else None) for ex, eq in pairs}
    if not norm:
        return {}, {}
    exercise_ids = sorted({ex for ex, _ in norm})
    catalog: dict[int, dict[str, Any]] = {}
    for i in range(0, len(exercise_ids), CATALOG_QUERY_BATCH_IDS):
        lote = exercise_ids[i:i + CATALOG_QUERY_BATCH_IDS]
        cur.execute(_EXERCISES_SQL.format(ids=",".join(["%s"] * len(lote))), tuple(lote))
        catalog.update({int(r["id"]): r for r in cur.fetchall() or []})
    names = {i: str(r["name"]) for i, r in catalog.items() if r.get("name") is not None}
    contributions = resolve_muscle_contributions_with_cursor(cur, set(exercise_ids))
    profiles = _profiles(cur, sorted((eq, ex) for ex, eq in norm if eq))

    out: dict[Pair, red.Candidate] = {}
    for ex_id, eq in norm:
        ex = catalog.get(ex_id)
        if ex is None:
            continue
        out[(ex_id, eq)] = red.Candidate(
            exercise_id=ex_id,
            equipment_id=eq,
            contributions=_groups(contributions.get(ex_id, [])),
            biomechanical_pattern=ex.get("biomechanical_pattern"),
            primary_joint=ex.get("primary_joint"),
            movement=ex.get("movement"),
            body_position=ex.get("body_position"),
            trajectory=ex.get("trajectory"),
            unilateral=None if ex.get("is_unilateral") is None else bool(ex["is_unilateral"]),
            resistance=profiles.get((eq, ex_id)) if eq else None,
        )
    return out, names
