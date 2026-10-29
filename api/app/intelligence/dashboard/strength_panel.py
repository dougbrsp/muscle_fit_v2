"""Painel de força do dashboard (M97 F4): e1RM dos compostos principais e o top
set de cada sessão de força.

Dono ÚNICO da regra de seleção dos "compostos principais" do painel
(`STRENGTH_PANEL_MAX_EXERCISES`, `select_main_lifts`): exercícios compostos do
catálogo (`exercise_catalog.is_compound`) com mais sessões de FORÇA (sem deload)
na janela; empate = o treinado mais recentemente, depois o menor id. Mapa
`docs/motores/FONTES_DA_VERDADE.md` §66.4.

Nenhum número novo de desempenho nasce aqui:

- o e1RM de cada exposição e a confiabilidade dele são os do dono
  `domain/e1rm` (via `exposure_service`); a linha é ÚNICA entre objetivos
  (decisão do usuário: e1RM e recorde únicos), com a exposição de força marcada;
- o recorde é o `records_all_goals` do drill-down (chave física, sem deload);
- o top set (carga × reps) é o `exposure_snapshot` (`best_load_kg` + reps das
  séries de topo por `comparability.top_load_reps`), só nas sessões de força;
- deload fica fora de tudo (predicado do dono `domain/deload`).

A tela só desenha.
"""

from __future__ import annotations

from typing import Any, Final, Mapping, Sequence

from ...domain.deload import sql_not_deload
from ...domain.training_goal import TrainingGoal, goal_label, goal_value, sql_goal_is
from ...observability import log_event
from ...set_types import sql_is_working, sql_set_confirmed
from .contracts import DataQuality, WindowInfo
from .exposure_service import (
    MAX_EXPOSURES,
    e1rm_series,
    exposure_snapshot,
    get_exposures_between,
    records_all_goals,
)
from .identity import require_user_id
from .metrics_registry import metric_version

# Quantos compostos o painel mostra (os mais treinados em força na janela).
STRENGTH_PANEL_MAX_EXERCISES: Final[int] = 4

# Objetivo cujas sessões o painel acompanha.
STRENGTH_PANEL_GOAL: Final[TrainingGoal] = TrainingGoal.STRENGTH

_CANONICAL = "COALESCE(elm.canonical_exercise_id, e.mapping_id)"

# Compostos com sessão de força na janela, os mais frequentes primeiro. Uma
# linha por exercício canônico (dezenas, no máximo). Só séries de trabalho
# confirmadas contam como sessão (o mesmo critério das exposições).
MAIN_LIFTS_SQL = f"""
SELECT
  {_CANONICAL}                 AS exercise_id,
  MAX(xc.name)                 AS exercise_name,
  COUNT(DISTINCT a.id)         AS sessions,
  MAX(a.local_date)            AS last_local_date
FROM activities a
JOIN exercises e ON e.activity_id = a.id
LEFT JOIN exercise_legacy_id_map elm ON elm.old_exercise_id = e.mapping_id
JOIN exercise_catalog xc ON xc.id = {_CANONICAL}
WHERE a.user_id = %s
  AND {sql_goal_is("e")}
  AND {sql_not_deload("a")}
  AND a.local_date BETWEEN %s AND %s
  AND xc.is_compound = 1
  AND EXISTS (
    SELECT 1 FROM exercise_sets es
    WHERE es.exercise_id = e.id AND {sql_is_working("es.set_type")} AND {sql_set_confirmed("es.reps_pending")}
  )
GROUP BY {_CANONICAL}
"""


def select_main_lifts(rows: Sequence[Mapping[str, Any]], limit: int = STRENGTH_PANEL_MAX_EXERCISES) -> list[dict[str, Any]]:
    """Regra pura da seleção: mais sessões de força primeiro; empate = a mais
    recente, depois o menor id (determinístico)."""
    candidates = [
        {
            "exercise_id": int(row["exercise_id"]),
            "name": row.get("exercise_name"),
            "sessions": int(row.get("sessions") or 0),
            "last_local_date": str(row.get("last_local_date") or ""),
        }
        for row in rows
        if row.get("exercise_id") is not None
    ]
    # Ordenacoes estaveis, da chave menos para a mais importante.
    candidates.sort(key=lambda item: item["exercise_id"])
    candidates.sort(key=lambda item: item["last_local_date"], reverse=True)
    candidates.sort(key=lambda item: item["sessions"], reverse=True)
    return candidates[: max(0, int(limit))]


def top_sets(every_goal: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Top set (carga de topo × maior número de reps nas séries de topo) de
    cada exposição de FORÇA, em ordem cronológica."""
    strength = goal_value(STRENGTH_PANEL_GOAL)
    out: list[dict[str, Any]] = []
    for item in every_goal:
        if item.get("_training_goal") != strength:
            continue
        snapshot = exposure_snapshot(item) or {}
        reps = snapshot.get("top_set_reps") or []
        out.append(
            {
                "activity_id": item.get("activity_id"),
                "local_date": item.get("local_date"),
                "load_kg": snapshot.get("best_load_kg"),
                "reps": max(reps) if reps else None,
                "top_sets_count": len(reps),
                "avg_rpe": snapshot.get("avg_rpe"),
                "e1rm": snapshot.get("e1rm"),
                "e1rm_reliable": bool(snapshot.get("e1rm_reliable")),
            }
        )
    return out


def lift_payload(lift: Mapping[str, Any], every_goal: list[dict[str, Any]]) -> dict[str, Any]:
    """Bloco de UM composto: linha única de e1RM (força destacada pelo
    objetivo do ponto), top sets de força e o recorde físico."""
    strength = goal_value(STRENGTH_PANEL_GOAL)
    strength_exposures = [item for item in every_goal if item.get("_training_goal") == strength]
    key = strength_exposures[-1]["_key"] if strength_exposures else (every_goal[-1]["_key"] if every_goal else None)
    records = records_all_goals(every_goal, strength_exposures, key)
    return {
        "exercise_id": lift["exercise_id"],
        "name": lift.get("name"),
        "strength_sessions": lift["sessions"],
        "e1rm_series": e1rm_series(every_goal, key),
        "top_sets": top_sets(every_goal),
        "best_e1rm": records.get("best_e1rm"),
    }


def build_strength_panel(conn: Any, *, user_id: Any, window: WindowInfo) -> dict[str, Any]:
    """Resposta de `GET /analytics/dashboard/strength-panel`. Sem sessão de força
    na janela: `lifts: []` com `INSUFFICIENT` (a tela esconde o painel)."""
    owner = require_user_id(user_id)
    strength = goal_value(STRENGTH_PANEL_GOAL)
    with conn.cursor() as cursor:
        cursor.execute(MAIN_LIFTS_SQL, (owner, strength, window.date_from, window.date_to))
        rows = cursor.fetchall() or []
    lifts = select_main_lifts(rows)

    payload_lifts = []
    for lift in lifts:
        every_goal = get_exposures_between(
            conn,
            lift["exercise_id"],
            user_id=owner,
            date_from=window.date_from,
            date_to=window.date_to,
            limit=MAX_EXPOSURES,
            all_goals=True,
            with_fatigue_context=False,
        )
        payload_lifts.append(lift_payload(lift, every_goal))

    log_event("dashboard.computed", block="strength_panel", user_id=owner, lifts=len(payload_lifts))
    limitations: list[str] = []
    if not payload_lifts:
        limitations.append("Nenhum exercício composto com sessão de força na janela.")
    return {
        "window": window,
        "training_goal": strength,
        "training_goal_label": goal_label(strength),
        "max_exercises": STRENGTH_PANEL_MAX_EXERCISES,
        "lifts": payload_lifts,
        "data_quality": DataQuality.HIGH if payload_lifts else DataQuality.INSUFFICIENT,
        "limitations": limitations,
        "metric_versions": {"exposure": metric_version("exposure"), "e1rm": metric_version("e1rm")},
    }


__all__ = [
    "MAIN_LIFTS_SQL",
    "STRENGTH_PANEL_GOAL",
    "STRENGTH_PANEL_MAX_EXERCISES",
    "build_strength_panel",
    "lift_payload",
    "select_main_lifts",
    "top_sets",
]
