import { fmtInt, fmtKg, fmtSignedPercent } from './dashboard-format';
import type { MuscleGroupPerformanceExercise, MuscleGroupProgressionItem } from './dashboard-v2-types';

/**
 * Texto do tooltip da coluna "Desempenho" de `/dashboard/progressao`.
 * Regras no servidor (`api/app/intelligence/muscle_group_performance.py`):
 * exercício com variação de e1RM acima de ±50% é descartado do cálculo
 * (atípico, mas continua listado) e o grupo só é classificado com ativação
 * somada dos exercícios usados ≥ 0,5.
 */
export const OUTLIER_LABEL = 'variação atípica — conferir registro';

const INSUFFICIENT_REASON_LABEL: Record<string, string> = {
  NO_COMPARABLE_EXERCISE: 'Nenhum exercício feito nas duas janelas no mesmo equipamento.',
  ALL_OUTLIERS: 'Todos os exercícios comparáveis tiveram variação atípica (acima de ±50%).',
  LOW_ACTIVATION: 'Poucos exercícios trabalham este grupo diretamente (ativação somada abaixo de 50%).',
};

function exerciseLine(ex: MuscleGroupPerformanceExercise): string {
  const base = `${ex.exercise_name ?? `Exercício ${ex.exercise_id}`}: ${fmtKg(ex.previous_e1rm_kg)} → ${fmtKg(
    ex.current_e1rm_kg,
  )} (${fmtSignedPercent(ex.change_pct)}, ativação ${Math.round(ex.stimulus_weight * 100)}%)`;
  return ex.outlier ? `${base} — ${OUTLIER_LABEL}` : base;
}

/** `null` quando não há nada a explicar (grupo sem exercício comparável). */
export function performanceTooltip(item: MuscleGroupProgressionItem): string | null {
  const exercises = item.performance_exercises ?? [];
  const reason = item.performance_insufficient_reason ?? null;
  if (reason === 'NO_COMPARABLE_EXERCISE' && exercises.length === 0) return null;
  const header = reason
    ? (INSUFFICIENT_REASON_LABEL[reason] ?? 'Sem leitura de desempenho.')
    : `e1RM estimado, mesma máquina nas duas janelas (${fmtInt(item.performance_exercises_compared)} exercícios):`;
  return [header, ...exercises.map(exerciseLine)].join('\n');
}
