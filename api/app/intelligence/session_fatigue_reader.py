"""Leitura (somente SELECT) do contexto de fadiga das sessoes REGISTRADAS.

Adaptador de I/O: le os exercicios e as series nao preparatorias das
atividades pedidas e as contribuicoes musculares, e entrega tudo ao dono
(`domain/prior_fatigue.py`) pela montagem pura de `domain/session_fatigue.py`.
Nenhuma regra aqui.

Custo fixo, sem N+1: uma leitura por pagina de `ACTIVITY_PAGE_SIZE`
atividades + duas do resolvedor de contribuicoes (contribuicoes e nomes dos
grupos), qualquer que seja o numero de exposicoes. O catalogo de tecnicas so e
lido se alguma serie for aplicacao de tecnica (`LazyCatalog`).
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

from ..domain.prior_fatigue import GroupFatigue, PriorFatigue
from ..domain.session_fatigue import (
    ExecutedExercise,
    contributions_from_rows,
    group_fatigue_before_exercise,
    group_fatigue_by_set_key,
    session_group_fatigue,
    session_prior_fatigue,
)
from ..set_types import sql_not_preparatory, sql_set_confirmed
from .muscle_contribution_resolver import resolve_muscle_contributions_with_cursor

# Atividades por leitura: ~25 series por sessao -> ~2.500 linhas estreitas por
# pagina, longe do teto de 1 MB da RDS Data API.
ACTIVITY_PAGE_SIZE = 100

# f-string só para o predicado das pendentes; `{{...}}` seguem para o `.format`.
SESSION_SETS_SQL = f"""
SELECT
  e.id                                              AS exercise_row_id,
  e.activity_id                                     AS activity_id,
  e.sequence                                        AS sequence,
  COALESCE(elm.canonical_exercise_id, e.mapping_id) AS exercise_id,
  es.id                                             AS set_id,
  es.set_number                                     AS set_number,
  es.set_type                                       AS set_type,
  es.reps                                           AS reps,
  es.rpe                                            AS rpe,
  es.technique_code                                 AS technique_code,
  es.anchor_set_number                              AS anchor_set_number
FROM activities a
JOIN exercises e ON e.activity_id = a.id
LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = e.mapping_id
LEFT JOIN exercise_sets es ON es.exercise_id = e.id AND {{not_preparatory}} AND {sql_set_confirmed('es.reps_pending')}
WHERE a.user_id = %s
  AND a.id IN ({{placeholders}})
ORDER BY e.activity_id ASC, e.sequence ASC, e.id ASC, es.set_number ASC, es.id ASC
"""


def build_executed_sessions(rows: Iterable[Mapping[str, Any]]) -> dict[int, list[ExecutedExercise]]:
    """Linhas planas -> `activity_id: [ExecutedExercise]` (funcao pura)."""
    by_activity: dict[int, dict[int, dict[str, Any]]] = {}
    for row in rows or ():
        activity_id = int(row["activity_id"])
        row_id = int(row["exercise_row_id"])
        exercise = by_activity.setdefault(activity_id, {}).get(row_id)
        if exercise is None:
            exercise_id = row.get("exercise_id")
            exercise = {
                "row_id": row_id,
                "sequence": int(row.get("sequence") or 0),
                "exercise_id": None if exercise_id is None else int(exercise_id),
                "sets": [],
            }
            by_activity[activity_id][row_id] = exercise
        if row.get("set_id") is not None:
            exercise["sets"].append(
                {
                    # `exercise_sets.id`: so identifica a serie (indicador de
                    # fadiga por serie do Modo Treino); ninguem conta por ele.
                    "set_id": row.get("set_id"),
                    "set_number": row.get("set_number"),
                    "set_type": row.get("set_type"),
                    "reps": row.get("reps"),
                    "rpe": row.get("rpe"),
                    "technique_code": row.get("technique_code"),
                    "anchor_set_number": row.get("anchor_set_number"),
                }
            )
    return {
        activity_id: [
            ExecutedExercise(
                row_id=item["row_id"],
                sequence=item["sequence"],
                exercise_id=item["exercise_id"],
                set_rows=tuple(item["sets"]),
            )
            for item in exercises.values()
        ]
        for activity_id, exercises in by_activity.items()
    }


def load_contributions(
    conn: Any,
    exercise_ids: Iterable[Optional[int]],
    group_names: Optional[Mapping[int, str]] = None,
) -> dict[int, tuple[Any, ...]]:
    """Contribuicoes (tipo do dono) por exercicio canonico, na conexao do
    chamador. `group_names` (nomes ja lidos pelo chamador) evita reler
    `muscle_groups`."""
    ids = {int(value) for value in exercise_ids if value is not None}
    if not ids:
        return {}
    with conn.cursor() as cursor:
        rows = resolve_muscle_contributions_with_cursor(cursor, ids, group_names)
    return contributions_from_rows(rows)


def load_executed_sessions(conn: Any, user_id: int, activity_ids: Iterable[Any]) -> dict[int, list[ExecutedExercise]]:
    """`activity_id` -> exercicios executados (series nao preparatorias), das
    atividades pedidas do usuario `user_id`."""
    ids = sorted({int(value) for value in activity_ids if value is not None})
    sessions: dict[int, list[ExecutedExercise]] = {}
    if not ids:
        return sessions
    with conn.cursor() as cursor:
        for start in range(0, len(ids), ACTIVITY_PAGE_SIZE):
            page = ids[start : start + ACTIVITY_PAGE_SIZE]
            cursor.execute(
                SESSION_SETS_SQL.format(
                    not_preparatory=sql_not_preparatory("es.set_type"),
                    placeholders=",".join(["%s"] * len(page)),
                ),
                (int(user_id), *page),
            )
            sessions.update(build_executed_sessions(cursor.fetchall() or []))
    return sessions


def _default_catalog(catalog: Optional[Mapping[str, Any]]) -> Mapping[str, Any]:
    if catalog is not None:
        return catalog
    from .technique_volume import new_catalog

    return new_catalog()


def load_session_prior_fatigue(
    conn: Any,
    user_id: int,
    activity_ids: Iterable[Any],
    *,
    catalog: Optional[Mapping[str, Any]] = None,
    group_names: Optional[Mapping[int, str]] = None,
) -> dict[int, Optional[PriorFatigue]]:
    """`exercises.id` -> fadiga previa do exercicio na sessao REAL, para todos
    os exercicios das atividades pedidas (do usuario `user_id`)."""
    sessions = load_executed_sessions(conn, user_id, activity_ids)
    if not sessions:
        return {}
    contributions = load_contributions(
        conn, (item.exercise_id for exercises in sessions.values() for item in exercises), group_names
    )
    catalog = _default_catalog(catalog)
    result: dict[int, Optional[PriorFatigue]] = {}
    for exercises in sessions.values():
        result.update(session_prior_fatigue(exercises, contributions, catalog))
    return result


def load_set_group_fatigue(
    conn: Any,
    user_id: int,
    activity_id: int,
    exercise_row_id: int,
    *,
    catalog: Optional[Mapping[str, Any]] = None,
) -> dict[int, Optional[GroupFatigue]]:
    """`exercise_sets.id` -> INDICADOR de fadiga do grupo (M79) antes de cada
    serie do exercicio `exercise_row_id` (linha de `exercises`) na sessao
    `activity_id`. Tres leituras fixas (series da sessao, contribuicoes, nomes)."""
    exercises = load_executed_sessions(conn, user_id, [activity_id]).get(int(activity_id), [])
    target = next((item for item in exercises if item.row_id == int(exercise_row_id)), None)
    if target is None:
        return {}
    contributions = load_contributions(conn, (item.exercise_id for item in exercises))
    timelines = session_group_fatigue(exercises, contributions, _default_catalog(catalog))
    return group_fatigue_by_set_key(target, timelines.get(target.row_id))


def load_current_group_fatigue(
    conn: Any,
    user_id: int,
    activity_id: int,
    exercise_id: int,
    *,
    catalog: Optional[Mapping[str, Any]] = None,
) -> Optional[GroupFatigue]:
    """"Fadiga atual do grupo" (M79): o indicador antes da 1a serie do
    exercicio canonico `exercise_id` na sessao EM ANDAMENTO `activity_id`
    (`session_fatigue.group_fatigue_before_exercise`)."""
    exercises = load_executed_sessions(conn, user_id, [activity_id]).get(int(activity_id), [])
    contributions = load_contributions(conn, [int(exercise_id), *(item.exercise_id for item in exercises)])
    return group_fatigue_before_exercise(exercises, contributions, int(exercise_id), _default_catalog(catalog))


__all__ = [
    "ACTIVITY_PAGE_SIZE",
    "SESSION_SETS_SQL",
    "build_executed_sessions",
    "load_contributions",
    "load_current_group_fatigue",
    "load_executed_sessions",
    "load_session_prior_fatigue",
    "load_set_group_fatigue",
]
