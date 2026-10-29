/**
 * Filtro de objetivo de treino dos gráficos de progressão (M97 F4).
 *
 * Só apresentação: vocabulário, rótulos e default chegam de `GET /training-goals`
 * (`lib/training-goals.ts`, F3; donos `api/app/domain/training_goal.py`). O
 * filtro vai para a API como `?goal=` e o backend filtra pelo predicado do
 * dono. Nenhuma regra numérica nem nome de objetivo escrito aqui — o e1RM, a
 * separação por objetivo e a marca de deload chegam prontos.
 *
 * Dois sabores, porque as rotas têm semânticas diferentes sem `goal`:
 * - progressão (drill-down `/dashboard/exercicios/[id]`): sempre UM objetivo
 *   (a progressão é separada por objetivo); `null` = o default do backend;
 * - tabela/gráfico de `/dashboard/progressao`: "Todos" = sem `goal`, a lista
 *   de sempre com todas as sessões.
 */

import { defaultGoal, goalSelectData, type TrainingGoalsResponse } from './training-goals';

/** "Todos os objetivos" (sem `goal` na query) — opção só de UI. */
export const ALL_GOALS = 'all' as const;
export const ALL_GOALS_LABEL = 'Todos';

export type GoalOption = { value: string; label: string };

/** Valor conhecido do vocabulário vindo da API (`null` = desconhecido/ausente). */
export function knownGoal(goals: TrainingGoalsResponse | null | undefined, value: unknown): string | null {
  if (typeof value !== 'string' || !goals) return null;
  const wanted = value.trim().toLowerCase();
  return goals.items.some((item) => item.value === wanted) ? wanted : null;
}

/** Opções do seletor da progressão (um objetivo por vez), rótulos da API. */
export function progressionGoalOptions(goals: TrainingGoalsResponse | null | undefined): GoalOption[] {
  return goalSelectData(goals);
}

/** Opções do seletor da tabela de `/dashboard/progressao` ("Todos" primeiro). */
export function sessionGoalFilterOptions(goals: TrainingGoalsResponse | null | undefined): GoalOption[] {
  return [{ value: ALL_GOALS, label: ALL_GOALS_LABEL }, ...goalSelectData(goals)];
}

/**
 * `?goal=` da query a partir do valor do seletor: "Todos"/desconhecido não
 * manda nada (a resposta de sempre).
 */
export function goalQueryParam(goals: TrainingGoalsResponse | null | undefined, selected: unknown): string | null {
  return knownGoal(goals, selected);
}

/** Valor exibido no seletor da progressão: o escolhido ou o default da API. */
export function progressionGoalSelectValue(
  goals: TrainingGoalsResponse | null | undefined,
  selected: string | null,
): string | null {
  return knownGoal(goals, selected) ?? defaultGoal(goals)?.value ?? null;
}

/** Valor do objetivo default (o do dono), para o destaque dos gráficos; `null` sem lista. */
export function defaultGoalValue(goals: TrainingGoalsResponse | null | undefined): string | null {
  return defaultGoal(goals)?.value ?? null;
}

/**
 * A execução é de um objetivo DIFERENTE do default (hoje, força)? É o que os
 * gráficos destacam. Sem a lista (`defaultValue` nulo) nada é destacado.
 */
export function isHighlightedGoal(value: string | null | undefined, defaultValue: string | null | undefined): boolean {
  return defaultValue != null && value != null && value !== '' && value !== defaultValue;
}
