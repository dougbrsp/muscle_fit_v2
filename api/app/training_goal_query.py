"""Parâmetro `goal` das rotas de leitura de progressão (M97 F1).

As rotas que listam progressão aceitam `?goal=` opcional: ausente = o default
do dono (`domain/training_goal.DEFAULT_GOAL`, hipertrofia - o comportamento de
sempre, sem mudar o contrato do frontend). Valor fora do vocabulário -> 422
com a mensagem do dono. Nenhuma regra aqui: só a ponte entre a leitura
estrita do dono e o `HTTPException`.
"""

from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, Query

from .domain.training_goal import InvalidTrainingGoal, goal_value, optional_goal_value

GOAL_QUERY_DESCRIPTION = "Objetivo de treino da progressão lida (ausente = default)."


def goal_query_value(goal: Optional[str]) -> str:
    """VALOR do objetivo pedido na query (ausente/vazio = default; inválido
    -> 422)."""
    try:
        return goal_value(optional_goal_value(goal))
    except InvalidTrainingGoal as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def goal_query(goal: Optional[str] = Query(default=None, description=GOAL_QUERY_DESCRIPTION)) -> str:
    """Dependência FastAPI: `goal: str = Depends(goal_query)`."""
    return goal_query_value(goal)


OPTIONAL_GOAL_QUERY_DESCRIPTION = "Filtro de objetivo de treino (ausente = todos os objetivos, como sempre)."


def optional_goal_query_value(goal: Optional[str]) -> Optional[str]:
    """VALOR do filtro opcional (M97 F4): ausente/vazio = `None` (sem filtro,
    a resposta de sempre); inválido -> 422."""
    try:
        return optional_goal_value(goal)
    except InvalidTrainingGoal as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def optional_goal_param(value: object) -> Optional[str]:
    """Normaliza o `goal` opcional DENTRO da rota. Pela HTTP, o FastAPI ja
    resolveu `optional_goal_query` (str ou None). Chamada direta da funcao da
    rota sem `goal` (scripts, testes de paridade) recebe o OBJETO default do
    `Depends`, que e verdadeiro e viraria um filtro sem linha nenhuma: tratado
    como ausente (a resposta de sempre)."""
    if value is None or isinstance(value, str):
        return optional_goal_query_value(value)
    return None


def optional_goal_query(
    goal: Optional[str] = Query(default=None, description=OPTIONAL_GOAL_QUERY_DESCRIPTION),
) -> Optional[str]:
    """Dependência FastAPI das leituras que, sem `goal`, nunca filtraram objetivo
    (gráficos de volume/tabela da progressão): `goal: Optional[str] =
    Depends(optional_goal_query)`."""
    return optional_goal_query_value(goal)


__all__ = [
    "GOAL_QUERY_DESCRIPTION",
    "OPTIONAL_GOAL_QUERY_DESCRIPTION",
    "goal_query",
    "goal_query_value",
    "optional_goal_param",
    "optional_goal_query",
    "optional_goal_query_value",
]
