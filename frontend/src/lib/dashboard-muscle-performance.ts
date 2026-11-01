import { fmtInt, fmtKg, fmtSignedPercent } from './dashboard-format';
import type { MuscleGroupPerformanceExercise, MuscleGroupProgressionItem } from './dashboard-v2-types';

/**
 * Texto do tooltip da coluna "Desempenho" de `/dashboard/progressao`.
 * O status do grupo é a agregação ponderada dos status dos exercícios (regra
 * do motor dono, a mesma da home). A variação de e1RM listada aqui é só
 * informativa. Exercício marcado como atípico é listado, mas fica fora do
 * percentual informativo.
 */
export const OUTLIER_LABEL = 'variação atípica — conferir registro';

const INSUFFICIENT_REASON_LABEL: Record<string, string> = {
  NO_COMPARABLE_EXERCISE: 'Nenhum exercício comparável na janela',
  LOW_ACTIVATION: 'Exercícios comparáveis com participação insuficiente no grupo',
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
    : `Variação informativa de e1RM, mesmo equipamento nas duas janelas (${fmtInt(item.performance_exercises_compared)} exercícios):`;
  return [header, ...exercises.map(exerciseLine)].join('\n');
}
