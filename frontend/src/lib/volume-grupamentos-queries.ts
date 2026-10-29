/**
 * Caminhos e chaves de cache das leituras de `/volume-grupamentos` que aceitam
 * o filtro de objetivo (M97 F4).
 *
 * Só apresentação: `goal` é o VALOR vindo de `GET /training-goals` (via
 * `lib/training-goal-filter.goalQueryParam`); "Todos" = `null` = sem `?goal=`,
 * a resposta de sempre. O backend filtra pelo predicado do dono
 * (`domain/training_goal.sql_goal_is`). A leitura de repetições
 * (`/analytics/training-volume`) não tem filtro de objetivo e não passa por aqui.
 */

import type { TrainingGoalValue } from './dashboard-v3-types';

export type RecentVolumeRange = { dateFrom: string; dateTo: string };

/**
 * `GET /analytics/muscle-groups-recent-exercise-volume`: com período, as datas
 * (o backend ignora o `limit`); sem período, o `limit`. `goal` só quando há
 * objetivo escolhido.
 */
export function recentExerciseVolumePath(
  limit: number,
  range?: RecentVolumeRange | null,
  goal?: TrainingGoalValue | null,
): string {
  const search = new URLSearchParams(
    range?.dateFrom && range?.dateTo ? { date_from: range.dateFrom, date_to: range.dateTo } : { limit: String(limit) },
  );
  if (goal) search.set('goal', goal);
  return `/analytics/muscle-groups-recent-exercise-volume?${search.toString()}`;
}

/** Chaves do React Query: o objetivo entra em todas (trocar o filtro troca o cache). */
export const volumeGrupamentosKeys = {
  recentVolume: (limit: number, goal: TrainingGoalValue | null = null) =>
    ['muscle-groups-recent-exercise-volume', limit, goal] as const,
  recentVolumeRange: (dateFrom: string | null, dateTo: string | null, goal: TrainingGoalValue | null = null) =>
    ['muscle-groups-recent-exercise-volume-range', dateFrom, dateTo, goal] as const,
  slotComparison: (limit: number, goal: TrainingGoalValue | null = null) =>
    ['muscle-groups-slot-comparison', limit, goal] as const,
};
