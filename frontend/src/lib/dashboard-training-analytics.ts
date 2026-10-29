import { ApiError } from './api';
import type { RpeDistributionItem, TrainingTrendItem } from './analytics-types';
import { fmtInt, fmtKgTotal, fmtRpe } from './dashboard-format';
import type { WeeklySummary, WorkoutsAnalysisTraining } from './workouts-analysis-types';

/**
 * Funções puras dos blocos que `/dashboard/treinos` herdou de `/treinos`
 * (resumo semanal, evolução por semana ISO, distribuição de RPE e análise
 * por IA). Nenhum cálculo de negócio: só formatação e forma dos dados para
 * os gráficos/tabelas — os números vêm prontos do backend.
 */

/**
 * O `weekly_summary` de `GET /analytics/training-evolution` é SEMPRE a
 * semana corrente (`TrainingLoadService.get_weekly_summary()` não recebe
 * `days`). O bloco pede a menor janela possível porque o endpoint também
 * agrega volume, correlação com o Intervals.icu e sessões pelo `days` — que
 * este bloco não lê. Trocar o período do dashboard não muda o resumo, então
 * não faz sentido refazer a consulta a cada troca.
 */
export const WEEKLY_SUMMARY_QUERY_DAYS = 7;

export type WeeklySummaryTile = {
  key: 'workouts' | 'sets' | 'volume' | 'high_rpe';
  label: string;
  value: string;
  hint: string;
};

/** Os quatro números do "Resumo da semana", na ordem da tela antiga. */
export function weeklySummaryTiles(summary: WeeklySummary): WeeklySummaryTile[] {
  return [
    {
      key: 'workouts',
      label: 'Treinos realizados',
      value: fmtInt(summary.completed_workouts),
      hint: `Previstos: ${fmtInt(summary.planned_workouts)}`,
    },
    {
      key: 'sets',
      label: 'Séries totais',
      value: fmtInt(summary.total_sets),
      hint: 'Nesta semana',
    },
    {
      key: 'volume',
      label: 'Volume total',
      value: fmtKgTotal(summary.total_volume_kg),
      hint: `RPE médio: ${fmtRpe(summary.avg_rpe)}`,
    },
    {
      key: 'high_rpe',
      label: 'Séries RPE alto',
      value: fmtInt(summary.high_rpe_sets),
      hint: 'RPE ≥ 9 nesta semana',
    },
  ];
}

export type IsoWeekRow = { periodo: string; volume: number; rpe: number | null };

/**
 * Linhas do gráfico "Evolução por semana ISO" (`training-trend?period=week`).
 * Volume ausente vira 0 no gráfico — o eixo é de tonelagem e a semana existe
 * na resposta (houve treino); RPE ausente continua `null` (sem RPE ≠ RPE 0).
 */
export function isoWeekRows(items: readonly TrainingTrendItem[] | null | undefined): IsoWeekRow[] {
  return (items ?? []).map((item) => ({
    periodo: item.period,
    volume: Number(item.volume_kg || 0),
    rpe: item.avg_rpe ?? null,
  }));
}

export type RpeRow = { rpe: string; sets: number };

/** Barras de "Distribuição de RPE": nota → séries. Ordem crescente de RPE. */
export function rpeRows(items: readonly RpeDistributionItem[] | null | undefined): RpeRow[] {
  return [...(items ?? [])]
    .sort((a, b) => a.rpe - b.rpe)
    .map((item) => ({ rpe: fmtRpe(item.rpe), sets: Number(item.sets_count || 0) }));
}

/** Total de séries com RPE no período (legenda do gráfico). */
export function totalRpeSets(rows: readonly RpeRow[]): number {
  return rows.reduce((sum, row) => sum + row.sets, 0);
}

export type AiAnalysisFailure = { title: string; detail: string };

/**
 * Mensagem da falha da análise por IA. 503 é "IA não configurada no
 * backend" — diferente de uma falha do job, que traz a mensagem real.
 */
export function aiAnalysisFailure(error: unknown): AiAnalysisFailure {
  if (error instanceof ApiError && error.status === 503) {
    return {
      title: 'IA indisponível no momento',
      detail:
        'O backend não está com a integração de IA configurada. Tente novamente mais tarde ou avise um administrador.',
    };
  }
  return {
    title: 'Não foi possível gerar a análise por IA',
    detail: error instanceof Error && error.message ? error.message : 'Não foi possível gerar a análise.',
  };
}

/** Há alguma tabela do snapshot da IA para mostrar? */
export function hasAiSnapshot(training: WorkoutsAnalysisTraining | null | undefined): boolean {
  if (!training) return false;
  return (
    (training.weekly_trend?.length ?? 0) > 0 ||
    (training.volume_overview?.length ?? 0) > 0 ||
    (training.recent_records?.length ?? 0) > 0 ||
    (training.exercise_progression?.length ?? 0) > 0 ||
    (training.recovery?.length ?? 0) > 0
  );
}
