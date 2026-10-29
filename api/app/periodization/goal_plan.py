"""Objetivo por dia na geração de Modelo por IA (M97 F5; mapa §68).

Pedido do usuário: o Modelo gerado pode ser HÍBRIDO (ex.: 5 dias, 2 de força e
3 de hipertrofia). O pedido (`PeriodizationDraftIn`) traz o objetivo do Modelo
(`training_goal`) e o de cada dia (`day_goals`); a LLM não escolhe objetivo:
o validador o aplica item a item, de forma determinística e auditada.

Precedência e regras (todas nos donos):
- objetivo do dia: `day_goals[sequence - 1]`; sem lista, o do Modelo; sem
  objetivo, o default (`training_goal.read_goal`);
- objetivo do item de equipamento no dia: `limits.day_item_goal` (composto = o
  do dia; isolador num dia de força = hipertrofia, FOR-023);
- item de grupo muscular (Modo A): o do dia (o motor escolhe o exercício);
- override gravado no item: o isolador fora do objetivo do dia leva o override
  EXPLÍCITO (fica em hipertrofia mesmo que o objetivo do Modelo mude depois);
  os demais só quando diferem do objetivo do Modelo (`None` = herda,
  `training_goal.resolve_goal`).

Python puro, sem I/O: a flag de composto (`exercise_catalog.is_compound`) chega
lida (`context.load_compound_flags`).
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from ..domain.training_goal import goal_label, goal_value, is_default
from .limits import day_item_goal

ITEM_KIND_EQUIPMENT = "equipment"
ITEM_KIND_MUSCLE_GROUP = "muscle_group"


def template_goal(request: Any) -> str:
    """Objetivo do Modelo a gerar (VALOR; ausente = default)."""
    return goal_value(getattr(request, "training_goal", None))


def declared_day_goals(request: Any) -> Optional[list[str]]:
    values = getattr(request, "day_goals", None)
    return list(values) if values else None


def day_goal(request: Any, sequence: int) -> str:
    """Objetivo do dia `sequence` (1..N): o declarado; senão o do Modelo."""
    declared = declared_day_goals(request)
    if declared and 1 <= int(sequence) <= len(declared):
        return goal_value(declared[int(sequence) - 1])
    return template_goal(request)


def declared_day_goal(request: Any, sequence: int) -> Optional[str]:
    """O objetivo do dia SÓ quando o pedido declarou objetivos (para o piso de
    exercícios do slot); `None` = deixa o piso decidir pelos itens, como antes."""
    if not declared_day_goals(request) and is_default(getattr(request, "training_goal", None)):
        return None
    return day_goal(request, sequence)


def all_goals(request: Any) -> list[str]:
    days = [day_goal(request, sequence) for sequence in range(1, int(request.days_per_cycle) + 1)]
    return [template_goal(request), *days]


def has_goal_plan(request: Any) -> bool:
    """O pedido usa algum objetivo NÃO-default (no Modelo ou num dia)? Sem isso
    nada desta etapa roda: o rascunho e o prompt de hipertrofia são os de sempre."""
    return any(not is_default(goal) for goal in all_goals(request))


def plan_for_prompt(request: Any) -> dict[str, Any]:
    """Contexto do prompt: objetivo do Modelo e de cada dia, com rótulo."""
    return {
        "template_goal": template_goal(request),
        "template_goal_label": goal_label(template_goal(request)),
        "days": [
            {"sequence": sequence, "training_goal": goal, "label": goal_label(goal)}
            for sequence in range(1, int(request.days_per_cycle) + 1)
            for goal in [day_goal(request, sequence)]
        ],
    }


def item_target_goal(day: Any, item_kind: Optional[str], is_compound: Optional[bool]) -> Optional[str]:
    """Objetivo que o item deve ter no dia (VALOR) ou `None` (cardio: sem
    objetivo). Equipamento sem flag conhecida = isolador (não leva carga
    máxima por engano)."""
    kind = item_kind or ITEM_KIND_EQUIPMENT
    if kind == ITEM_KIND_EQUIPMENT:
        return day_item_goal(day, bool(is_compound)).value
    if kind == ITEM_KIND_MUSCLE_GROUP:
        return goal_value(day)
    return None


def override_for(target: str, template: str) -> Optional[str]:
    """Override a gravar no item: `None` quando o alvo é o do Modelo."""
    return None if goal_value(target) == goal_value(template) else goal_value(target)


def compound_of(item: Any, compound_flags: Mapping[int, bool]) -> Optional[bool]:
    mapping_id = getattr(item, "mapping_id", None)
    return compound_flags.get(int(mapping_id)) if mapping_id else None


__all__ = [
    "all_goals",
    "compound_of",
    "day_goal",
    "declared_day_goal",
    "declared_day_goals",
    "has_goal_plan",
    "item_target_goal",
    "override_for",
    "plan_for_prompt",
    "template_goal",
]
