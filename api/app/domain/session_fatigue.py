"""Montagem das entradas do dono `domain/prior_fatigue.py` para uma SESSAO -
registrada (exercicios executados, na ordem) ou planejada (itens do slot /
ficha, na ordem). Python puro, sem I/O.

Nenhuma regra de fadiga aqui: a conta (musculo principal, series previas x
peso, faixas) e TODA de `prior_fatigue.prior_fatigue_for_sequence`. Este
modulo so responde "quantas series cada item tem" e "em que ordem", com as
MESMAS fontes dos outros motores:

- series EXECUTADAS = linhas nao preparatorias de `exercise_sets`, cada uma
  pela sua unidade de serie equivalente (`technique_accounting.realized_set_units`:
  1 por serie de trabalho/backoff, a fatia do bloco por aplicacao de tecnica)
  - a mesma unidade do volume realizado;
- series PLANEJADAS = `technique_volume.planned_item_sets` (calculadas pelo
  chamador, que tem o catalogo), as mesmas do preview do Modelo;
- ordem = `exercises.sequence` (desempate pelo id da linha) na sessao
  registrada; a ordem dos itens na sessao planejada;
- pesos = contribuicoes de `exercise_muscle_contributions` resolvidas pelo
  dono de "Pesos por papel" (`muscle_contribution_resolver`), convertidas por
  `prior_fatigue.contribution_from_mapping`.

Quem consome: a comparabilidade (`intelligence/dashboard/exposure_service.py`,
contexto de cada exposicao) e a progressao (`progression_state` monta o
contexto de cada execucao para o avaliador; `deterministic_prescription`, o
da posicao planejada).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence

from .prior_fatigue import (
    FatigueItem,
    GroupFatigue,
    GroupFatigueItem,
    GroupFatigueTimeline,
    MuscleContribution,
    PriorFatigue,
    PriorFatigueBand,
    context_band,
    contribution_from_mapping,
    group_fatigue_for_sequence,
    prior_fatigue_for_sequence,
)
from .technique_accounting import realized_set_units

ContributionsByExercise = Mapping[int, Sequence[MuscleContribution]]


@dataclass(frozen=True)
class ExecutedExercise:
    """Um exercicio (linha de `exercises`) de uma sessao registrada.

    `set_rows`: as series NAO preparatorias, com `set_number`, `set_type`,
    `reps`, `rpe`, `technique_code`, `anchor_set_number` (o que
    `realized_set_units` le)."""

    row_id: int
    sequence: int
    exercise_id: Optional[int]
    set_rows: tuple[Mapping[str, Any], ...] = ()


def contributions_from_rows(rows_by_exercise: Mapping[int, Iterable[Mapping[str, Any]]]) -> dict[int, tuple[MuscleContribution, ...]]:
    """Linhas do resolvedor (`resolve_muscle_contributions_with_cursor`) ->
    tipo do dono."""
    return {
        int(exercise_id): tuple(contribution_from_mapping(row) for row in rows)
        for exercise_id, rows in (rows_by_exercise or {}).items()
    }


def executed_set_units(exercise: ExecutedExercise, catalog: Optional[Mapping[str, Any]] = None) -> tuple[float, ...]:
    """Unidade de serie equivalente de CADA serie executada, na ordem de
    `set_rows` (unidade do volume, `realized_set_units`)."""
    if not exercise.set_rows:
        return ()
    rows = [{**dict(row), "_exercise_row": exercise.row_id} for row in exercise.set_rows]
    return tuple(float(unit) for unit in realized_set_units(rows, catalog=catalog, exercise_key="_exercise_row"))


def executed_sets(exercise: ExecutedExercise, catalog: Optional[Mapping[str, Any]] = None) -> float:
    """Series equivalentes executadas do exercicio (unidade do volume)."""
    return float(sum(executed_set_units(exercise, catalog)))


def _ordered(exercises: Sequence[ExecutedExercise]) -> list[ExecutedExercise]:
    return sorted(exercises, key=lambda item: (int(item.sequence or 0), int(item.row_id)))


def session_prior_fatigue(
    exercises: Sequence[ExecutedExercise],
    contributions: ContributionsByExercise,
    catalog: Optional[Mapping[str, Any]] = None,
) -> dict[int, Optional[PriorFatigue]]:
    """`exercises.id` -> fadiga previa do exercicio NA SESSAO REAL (dono
    `prior_fatigue_for_sequence`), com os exercicios executados antes dele,
    na ordem de execucao."""
    ordered = _ordered(exercises)
    items = [
        FatigueItem(
            sets=executed_sets(item, catalog),
            contributions=tuple(contributions.get(int(item.exercise_id), ())) if item.exercise_id is not None else (),
        )
        for item in ordered
    ]
    return {item.row_id: prior for item, prior in zip(ordered, prior_fatigue_for_sequence(items))}


def session_contexts(
    exercises: Sequence[ExecutedExercise],
    contributions: ContributionsByExercise,
    catalog: Optional[Mapping[str, Any]] = None,
) -> dict[int, PriorFatigueBand]:
    """`exercises.id` -> CONTEXTO de fadiga (`prior_fatigue.context_band`)."""
    return {row_id: context_band(prior) for row_id, prior in session_prior_fatigue(exercises, contributions, catalog).items()}


def planned_contexts(
    items: Sequence[tuple[float, Optional[int]]],
    contributions: ContributionsByExercise,
) -> list[PriorFatigueBand]:
    """Contexto de cada item de uma sessao PLANEJADA, na ordem dada:
    `items` = `[(series planejadas, exercise_id)]` (series ja com o
    equivalente de tecnica de `planned_item_sets`)."""
    fatigue_items = [
        FatigueItem(
            sets=float(sets or 0.0),
            contributions=tuple(contributions.get(int(exercise_id), ())) if exercise_id is not None else (),
        )
        for sets, exercise_id in items
    ]
    return [context_band(prior) for prior in prior_fatigue_for_sequence(fatigue_items)]


def _contributions_of(exercise_id: Optional[int], contributions: ContributionsByExercise) -> tuple[MuscleContribution, ...]:
    return tuple(contributions.get(int(exercise_id), ())) if exercise_id is not None else ()


def _group_items(
    ordered: Sequence[ExecutedExercise],
    contributions: ContributionsByExercise,
    catalog: Optional[Mapping[str, Any]],
) -> list[GroupFatigueItem]:
    return [
        GroupFatigueItem(
            set_units=executed_set_units(item, catalog),
            contributions=_contributions_of(item.exercise_id, contributions),
        )
        for item in ordered
    ]


def session_group_fatigue(
    exercises: Sequence[ExecutedExercise],
    contributions: ContributionsByExercise,
    catalog: Optional[Mapping[str, Any]] = None,
) -> dict[int, GroupFatigueTimeline]:
    """`exercises.id` -> INDICADOR de fadiga do grupo (M79, dono
    `prior_fatigue.group_fatigue_for_sequence`) da sessao REAL: antes do bloco
    e antes de cada serie (`per_set` na ordem de `set_rows`)."""
    ordered = _ordered(exercises)
    items = _group_items(ordered, contributions, catalog)
    return {item.row_id: timeline for item, timeline in zip(ordered, group_fatigue_for_sequence(items))}


def group_fatigue_by_set_key(
    exercise: ExecutedExercise, timeline: Optional[GroupFatigueTimeline], key: str = "set_id"
) -> dict[Any, Optional[GroupFatigue]]:
    """Leitura de cada serie do exercicio indexada pela chave `key` de
    `set_rows` (ex.: `exercise_sets.id`), para anexar ao payload."""
    if timeline is None:
        return {}
    return {
        row.get(key): reading
        for row, reading in zip(exercise.set_rows, timeline.per_set)
        if row.get(key) is not None
    }


def group_fatigue_before_exercise(
    exercises: Sequence[ExecutedExercise],
    contributions: ContributionsByExercise,
    exercise_id: int,
    catalog: Optional[Mapping[str, Any]] = None,
) -> Optional[GroupFatigue]:
    """"Fadiga atual do grupo" no Modo Treino: o indicador ANTES da 1a serie do
    exercicio canonico `exercise_id` na sessao EM ANDAMENTO (`exercises` =
    os ja registrados nela). Ja registrado (o bloco comecou): a leitura antes
    da 1a serie da ocorrencia mais recente (maior `sequence`). Ainda nao
    registrado: entra como o PROXIMO exercicio, depois de todos os
    registrados."""
    target = int(exercise_id)
    matches = [item for item in exercises if item.exercise_id is not None and int(item.exercise_id) == target]
    if matches:
        current = max(matches, key=lambda item: (int(item.sequence or 0), int(item.row_id)))
        timeline = session_group_fatigue(exercises, contributions, catalog).get(current.row_id)
        return timeline.before if timeline is not None else None
    ordered = _ordered(exercises)
    items = _group_items(ordered, contributions, catalog)
    items.append(GroupFatigueItem(set_units=(), contributions=_contributions_of(target, contributions)))
    return group_fatigue_for_sequence(items)[-1].before


def executions_from_session(session: Any) -> list[ExecutedExercise]:
    """`training_history.SessionRecord` -> exercicios executados (as series do
    historico ja vem sem as preparatorias)."""
    return [
        ExecutedExercise(
            row_id=int(execution.exercise_row_id),
            sequence=int(execution.sequence or 0),
            exercise_id=execution.exercise_id,
            set_rows=tuple(
                {
                    "set_number": record.set_number,
                    "set_type": record.set_type,
                    "reps": record.reps,
                    "rpe": record.rpe,
                    "technique_code": record.technique_code,
                    "anchor_set_number": record.anchor_set_number,
                }
                for record in execution.sets
            ),
        )
        for execution in session.exercises
    ]


__all__ = [
    "ExecutedExercise",
    "contributions_from_rows",
    "executed_set_units",
    "executed_sets",
    "group_fatigue_before_exercise",
    "group_fatigue_by_set_key",
    "executions_from_session",
    "planned_contexts",
    "session_contexts",
    "session_group_fatigue",
    "session_prior_fatigue",
]
