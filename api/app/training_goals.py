"""Objetivos de treino para as telas (M97 F3): `GET /training-goals`.

Rota de LEITURA, sem banco: devolve o vocabulário, o rótulo e os defaults de
prescrição de cada objetivo, montados pelos donos. O frontend (editor de
Modelo, Modo Treino) não escreve número nem nome de objetivo à mão (mapa
`docs/motores/FONTES_DA_VERDADE.md` §66.3):

- vocabulário, default e rótulo: `domain/training_goal.py`;
- reps/RPE/descanso/séries e tetos de sanidade: `prescription_defaults.defaults_for`
  (os campos `sets_count`, `rpe_target` e `rest_seconds_sanity_max` reservados na F2);
- RPE-alvo, teto e técnicas fora da política do objetivo: `domain/training_safety.py`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from .domain import prescription_defaults, training_goal, training_safety
from .security import get_current_identity

router = APIRouter(prefix="/training-goals", tags=["training-goals"], dependencies=[Depends(get_current_identity)])


def goal_entry(goal: training_goal.TrainingGoal) -> dict[str, Any]:
    """Um objetivo, pronto para a tela."""
    return {
        "value": goal.value,
        "label": training_goal.goal_label(goal),
        "is_default": goal is training_goal.DEFAULT_GOAL,
        "defaults": prescription_defaults.defaults_for(goal).to_dict(),
        # Item sem descanso próprio: "da geração" (intervalo pedido) ou o do perfil.
        "rest_follows_generation_interval": prescription_defaults.rest_follows_generation_interval(goal),
        "target_rpe": training_safety.goal_target_rpe(goal),
        "rpe_ceiling": training_safety.goal_rpe_ceiling(goal),
        "disallowed_techniques": sorted(training_safety.goal_disallowed_techniques(goal)),
    }


def training_goals_payload() -> dict[str, Any]:
    return {
        "default": training_goal.DEFAULT_GOAL.value,
        "items": [goal_entry(goal) for goal in training_goal.TrainingGoal],
    }


@router.get("")
def list_training_goals():
    return training_goals_payload()


__all__ = ["goal_entry", "router", "training_goals_payload"]
