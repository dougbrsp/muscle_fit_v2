/**
 * Vista PURA do painel de força (M97 F4, `GET /analytics/dashboard/strength-panel`).
 *
 * Nada aqui calcula desempenho: os compostos principais (seleção do dono
 * `api/app/intelligence/dashboard/strength_panel.py`), o e1RM de cada ponto, o
 * recorde e o top set de cada sessão de força chegam prontos. Esta camada só
 * escolhe o que mostrar em cada linha.
 */

import { EM_DASH, fmtDate, fmtKg } from './dashboard-format';
import type { StrengthLift, StrengthPanelResponse, StrengthTopSet } from './dashboard-v3-types';
import { buildUnifiedE1rmChart, type UnifiedE1rmChart } from './exercise-progress';

export type StrengthLiftRow = {
  exerciseId: number;
  name: string;
  strengthSessions: number;
  /** e1RM mais recente da linha única (mesma chave física do recorde). */
  latestE1rm: number | null;
  latestE1rmDate: string | null;
  bestE1rm: number | null;
  bestE1rmReliable: boolean | null;
  lastTopSet: StrengthTopSet | null;
  chart: UnifiedE1rmChart;
};

/** "100 kg × 5" (carga de topo × maior número de reps nas séries de topo). */
export function topSetText(topSet: StrengthTopSet | null | undefined): string {
  if (!topSet || topSet.load_kg == null) return EM_DASH;
  return topSet.reps != null ? `${fmtKg(topSet.load_kg)} × ${topSet.reps}` : fmtKg(topSet.load_kg);
}

export function topSetCaption(topSet: StrengthTopSet | null | undefined): string | null {
  if (!topSet) return null;
  return fmtDate(topSet.local_date);
}

export function strengthLiftRow(lift: StrengthLift, panelGoal: string | null = null): StrengthLiftRow {
  // Destaque = o objetivo que o painel acompanha (`training_goal` da resposta).
  const chart = buildUnifiedE1rmChart(lift.e1rm_series, [], (goal) => panelGoal != null && goal === panelGoal);
  const withValue = chart.points.filter((point) => point.value != null);
  const latest = withValue.length > 0 ? withValue[withValue.length - 1] : null;
  const lastTopSet = lift.top_sets.length > 0 ? lift.top_sets[lift.top_sets.length - 1] : null;
  return {
    exerciseId: lift.exercise_id,
    name: lift.name || `Exercício ${lift.exercise_id}`,
    strengthSessions: lift.strength_sessions,
    latestE1rm: latest?.value ?? null,
    latestE1rmDate: latest?.localDate ?? null,
    bestE1rm: lift.best_e1rm?.value ?? null,
    bestE1rmReliable: lift.best_e1rm?.reliable ?? null,
    lastTopSet,
    chart,
  };
}

/** Linhas do painel; vazio = sem sessão de força na janela (a tela esconde). */
export function strengthPanelRows(panel: StrengthPanelResponse | null | undefined): StrengthLiftRow[] {
  return (panel?.lifts ?? []).map((lift) => strengthLiftRow(lift, panel?.training_goal ?? null));
}
