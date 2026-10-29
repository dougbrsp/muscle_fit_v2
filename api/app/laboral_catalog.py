"""Leitura do exercício laboral no catálogo (M100; dono da regra:
`app/domain/laboral.py`).

Uma leitura só (`SELECT ... FROM exercise_catalog WHERE id IN (...)`) por
chamada; nenhuma regra mora aqui.
"""

from __future__ import annotations

from typing import Any, Iterable

from .domain.laboral import LaboralDefaults, defaults_from_row, is_laboral_flag

_SQL = (
    "SELECT id, is_laboral, laboral_default_sets, laboral_default_reps, laboral_default_load_kg "
    "FROM exercise_catalog WHERE id IN ({placeholders}) AND is_laboral = 1"
)


def _ids(exercise_ids: Iterable[Any]) -> list[int]:
    out: set[int] = set()
    for value in exercise_ids or ():
        try:
            if value is not None:
                out.add(int(value))
        except (TypeError, ValueError):
            continue
    return sorted(out)


def load_laboral_defaults(conn: Any, exercise_ids: Iterable[Any]) -> dict[int, LaboralDefaults]:
    """`exercise_id` -> padrão, SÓ dos exercícios marcados como laborais."""
    ids = _ids(exercise_ids)
    if not ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(_SQL.format(placeholders=", ".join(["%s"] * len(ids))), tuple(ids))
        rows = cur.fetchall() or []
    return {int(row["id"]): defaults_from_row(row) for row in rows if is_laboral_flag(row.get("is_laboral"))}


def load_laboral_ids(conn: Any, exercise_ids: Iterable[Any]) -> frozenset[int]:
    return frozenset(load_laboral_defaults(conn, exercise_ids))


def load_laboral_ids_with(fetch_all: Any, exercise_ids: Iterable[Any]) -> frozenset[int]:
    """Mesma leitura para quem só tem `fetch_all(sql, params)` (VolumeService)."""
    ids = _ids(exercise_ids)
    if not ids:
        return frozenset()
    rows = fetch_all(_SQL.format(placeholders=", ".join(["%s"] * len(ids))), tuple(ids)) or []
    return frozenset(int(row["id"]) for row in rows if is_laboral_flag(row.get("is_laboral")))
