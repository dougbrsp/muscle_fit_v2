"""Carimbo do objetivo de treino nas linhas da ficha, PELO SERVIDOR (M97 F1).

Pergunta que este modulo responde: **de que objetivo e cada linha desta
ficha** - sem depender do cliente ecoar `training_goal` do rascunho (o
`frontend/src/lib/workout-plan-payload.ts` nao o manda, dívida da F0).

A regra de precedencia e do dono `domain/training_goal.py` (override do item
do slot > objetivo do Modelo > default); aqui so se acha o item de cada linha,
com o MESMO casamento do congelamento:

- Modo B (`equipment_series`): `freeze_mode_b.match_rows_to_model_items`
  (por exercicio, depois por posicao M6). Linha sem item (acrescentada a mao)
  = o objetivo do Modelo.
- Modo A (`percent_budget`): o item e do GRUPO muscular - o do grupo PRIMARY
  do exercicio da linha (`training_goal.goal_by_group`); grupo sem item = o do
  Modelo.
- Ficha sem Modelo (treino livre/manual): a linha fica como esta (o valor ja
  gravado, ou o default da coluna).

Vale para qualquer gerador que tenha produzido o rascunho (deterministico ou
os legados `mode_a_legacy`/`mode_b_legacy`, fallback): o objetivo sai do
Modelo da ficha, nunca do rascunho.

A versao do Modelo e a da FICHA (066; ficha antiga = a vigente), como no
congelamento. Duas leituras (cabecalho + itens do slot) e um UPDATE por linha
que MUDA - numa ficha de Modelo sem objetivo (todas hoje), nenhum UPDATE.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from ..domain.training_goal import TRAINING_GOAL_COLUMN, goal_by_group, goal_value, resolved_goal_value

VOLUME_MODE_EQUIPMENT_SERIES = "equipment_series"
VOLUME_MODE_PERCENT_BUDGET = "percent_budget"

# Cabecalho: Modelo + versao da ficha (ou a vigente) e o objetivo DELA.
PLAN_GOAL_CONTEXT_SQL = (
    "SELECT p.template_id, p.template_slot_sequence, v.id AS version_id, "
    "       CASE WHEN v.id IS NULL THEN t.volume_mode ELSE v.volume_mode END AS volume_mode, "
    f"      CASE WHEN v.id IS NULL THEN t.{TRAINING_GOAL_COLUMN} ELSE v.{TRAINING_GOAL_COLUMN} END "
    f"        AS template_{TRAINING_GOAL_COLUMN} "
    "FROM workout_plans p LEFT JOIN workout_plan_templates t ON t.id = p.template_id "
    "LEFT JOIN workout_plan_template_versions v ON v.template_id = p.template_id "
    "  AND (v.id = p.template_version_id "
    "       OR (p.template_version_id IS NULL AND v.current_template_id = p.template_id)) "
    "WHERE p.id = %s"
)

# Itens do slot da versao (o override do objetivo; NULL = herda).
SLOT_ITEMS_GOAL_SQL = (
    "SELECT i.item_order, i.item_kind, i.muscle_group_id, i.mapping_id, "
    f"      i.{TRAINING_GOAL_COLUMN} "
    "FROM workout_plan_template_slot_items i "
    "JOIN workout_plan_template_slots s ON s.id = i.slot_id "
    "WHERE s.template_id = %s AND s.version_id = %s AND s.sequence = %s "
    "ORDER BY i.item_order ASC"
)


def _plan_context(conn: Any, plan_id: int) -> Optional[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(PLAN_GOAL_CONTEXT_SQL, (int(plan_id),))
        plan = cur.fetchone()
        if not plan or not plan.get("template_id"):
            return None
        plan = dict(plan)
        cur.execute(
            SLOT_ITEMS_GOAL_SQL,
            (int(plan["template_id"]), plan.get("version_id"), plan.get("template_slot_sequence")),
        )
        plan["items"] = [dict(item) for item in cur.fetchall() or []]
    return plan


def resolve_row_goals(
    conn: Any, rows: Sequence[Mapping[str, Any]], plan: Mapping[str, Any]
) -> dict[int, str]:
    """`workout_plan_exercise_id` -> VALOR do objetivo resolvido de cada
    linha, a partir do Modelo da ficha (`plan` = `_plan_context`)."""
    template_goal = plan.get(f"template_{TRAINING_GOAL_COLUMN}")
    items = list(plan.get("items") or [])
    default_for_plan = resolved_goal_value(None, template_goal)
    result: dict[int, str] = {int(row["workout_plan_exercise_id"]): default_for_plan for row in rows}
    volume_mode = plan.get("volume_mode")
    if volume_mode == VOLUME_MODE_EQUIPMENT_SERIES:
        from .freeze_mode_b import match_rows_to_model_items

        equipment_items = [item for item in items if item.get("item_kind") == "equipment"]
        matched = match_rows_to_model_items(conn, rows, equipment_items)
        for row_id, item in matched.items():
            result[int(row_id)] = resolved_goal_value(item.get(TRAINING_GOAL_COLUMN), template_goal)
    elif volume_mode == VOLUME_MODE_PERCENT_BUDGET:
        from .. import progression_state as ps

        goals = goal_by_group(items, template_goal)
        mapped = [row for row in rows if row.get("mapping_id")]
        if goals and mapped:
            primary, _weights = ps.load_primary_groups(conn, [int(row["mapping_id"]) for row in mapped])
            for row in mapped:
                group_id = primary.get(int(row["mapping_id"]))
                if group_id is not None and int(group_id) in goals:
                    result[int(row["workout_plan_exercise_id"])] = goals[int(group_id)]
    return result


def stamp_plan_row_goals(conn: Any, plan_id: int, rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Grava em `workout_plan_exercises.training_goal` o objetivo resolvido de
    cada linha de `rows` (as linhas que o congelamento vai congelar) e devolve
    as MESMAS linhas com o valor novo - o snapshot (`prescription_json`) e a
    progressao do congelamento leem dele. Ficha sem Modelo: inalteradas."""
    rows = [dict(row) for row in rows]
    if not rows:
        return rows
    plan = _plan_context(conn, plan_id)
    if plan is None:
        return rows
    resolved = resolve_row_goals(conn, rows, plan)
    with conn.cursor() as cur:
        for row in rows:
            row_id = int(row["workout_plan_exercise_id"])
            goal = resolved.get(row_id)
            if goal is None or goal == goal_value(row.get(TRAINING_GOAL_COLUMN)):
                row[TRAINING_GOAL_COLUMN] = goal_value(row.get(TRAINING_GOAL_COLUMN))
                continue
            cur.execute(
                f"UPDATE workout_plan_exercises SET {TRAINING_GOAL_COLUMN} = %s WHERE id = %s",
                (goal, row_id),
            )
            row[TRAINING_GOAL_COLUMN] = goal
    return rows


__all__ = ["resolve_row_goals", "stamp_plan_row_goals"]
