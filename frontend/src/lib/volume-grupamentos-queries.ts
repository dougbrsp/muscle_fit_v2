/**
 * Caminhos e chaves de cache das leituras de `/volume-grupamentos`.
 *
 * Só apresentação: `goal` é o VALOR vindo de `GET /training-goals` (via
 * `lib/training-goal-filter.goalQueryParam`); "Todos" = `null` = sem `?goal=`,
 * a resposta de sempre. O backend filtra pelo predicado do dono
 * (`domain/training_goal.sql_goal_is`). `include` pede os blocos prontos do
 * backend (`weekly`, `equipment_weekly`, `heatmap`; dono:
 * `intelligence/slot_session_comparison.py`); sem `include`, a resposta antiga.
 */

import type { TrainingGoalValue } from './dashboard-v3-types';
import type { TrainingVolumeGranularity } from './training-volume-types';

export type RecentVolumeRange = { dateFrom: string; dateTo: string };
export type RecentVolumeInclude = 'weekly' | 'equipment_weekly' | 'heatmap';
export type RecentVolumeOptions = { include?: readonly RecentVolumeInclude[]; activityId?: number | null };

/**
 * `GET /analytics/muscle-groups-recent-exercise-volume`: com período, as datas
 * (o backend ignora o `limit`); sem período, o `limit`. `goal` só quando há
 * objetivo escolhido; `activity_id` restringe só o `heatmap` a uma sessão.
 */
export function recentExerciseVolumePath(
  limit: number,
  range?: RecentVolumeRange | null,
  goal?: TrainingGoalValue | null,
  options: RecentVolumeOptions = {},
): string {
  const search = new URLSearchParams(
    range?.dateFrom && range?.dateTo ? { date_from: range.dateFrom, date_to: range.dateTo } : { limit: String(limit) },
  );
  if (goal) search.set('goal', goal);
  if (options.include?.length) search.set('include', options.include.join(','));
  if (options.activityId != null) search.set('activity_id', String(options.activityId));
  return `/analytics/muscle-groups-recent-exercise-volume?${search.toString()}`;
}

/** `GET /analytics/training-volume`: `granularity` pede o `period_comparison` pronto. */
export function trainingVolumePath(days: number, granularity: TrainingVolumeGranularity): string {
  return `/analytics/training-volume?days=${days}&granularity=${granularity}`;
}

/** Chaves do React Query: cada combinação de parâmetros tem a sua. */
export const volumeGrupamentosKeys = {
  recentVolume: (limit: number, goal: TrainingGoalValue | null = null, include: readonly RecentVolumeInclude[] = []) =>
    ['muscle-groups-recent-exercise-volume', limit, goal, include.join(',')] as const,
  heatmap: (range: RecentVolumeRange | null, activityId: number | null, goal: TrainingGoalValue | null = null) =>
    ['muscle-groups-recent-exercise-volume-heatmap', range?.dateFrom ?? null, range?.dateTo ?? null, activityId, goal] as const,
  slotComparison: (limit: number, goal: TrainingGoalValue | null = null) =>
    ['muscle-groups-slot-comparison', limit, goal] as const,
  trainingVolume: (days: number, granularity: TrainingVolumeGranularity) => ['training-volume', days, granularity] as const,
};
