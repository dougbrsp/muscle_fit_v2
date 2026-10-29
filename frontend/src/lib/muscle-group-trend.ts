// Lógica pura da "Tendência por grupo muscular" (GET
// /analytics/muscle-groups-mapped-trend), extraída de /grupos-musculares para
// o bloco reutilizável em /dashboard/musculos (`MuscleGroupTrend.tsx`).
//
// Tudo aqui é função pura sobre os filtros e as linhas do payload — sem
// estado nem efeito — para que o componente só DERIVE valores (useMemo) e
// nunca faça setState em efeito/render (ver o loop React #185 corrigido no
// PR #290 e `muscle-groups-selection.ts`).

import type {
  MuscleGroupsMappedMetric,
  MuscleGroupsMappedPeriod,
  MuscleGroupsMappedTrendRow,
} from '@/lib/muscle-groups-types';

/** Filtros que controlam o gráfico (o MultiSelect de grupos é estado à parte). */
export type MuscleGroupTrendFilters = {
  period: MuscleGroupsMappedPeriod;
  metric: MuscleGroupsMappedMetric;
  /** Janela em dias, como string (valor do Select). */
  days: string;
  includeSecondary: boolean;
};

export const DEFAULT_MUSCLE_GROUP_TREND_FILTERS: MuscleGroupTrendFilters = {
  period: 'weekly',
  metric: 'volume',
  days: '180',
  includeSecondary: false,
};

export const TREND_DAY_OPTIONS = [
  { value: '30', label: '30 dias' },
  { value: '90', label: '90 dias' },
  { value: '180', label: '180 dias' },
  { value: '365', label: '12 meses' },
];

// Alinhado ao pattern "^(daily|weekly|monthly|quarterly|yearly)$" do backend.
export const TREND_PERIOD_OPTIONS: { value: MuscleGroupsMappedPeriod; label: string }[] = [
  { value: 'daily', label: 'Diário' },
  { value: 'weekly', label: 'Semanal' },
  { value: 'monthly', label: 'Mensal' },
  { value: 'quarterly', label: 'Trimestral' },
  { value: 'yearly', label: 'Anual' },
];

export const TREND_PERIOD_DESCRIPTION: Record<MuscleGroupsMappedPeriod, string> = {
  daily: 'dia',
  weekly: 'semana',
  monthly: 'mês',
  quarterly: 'trimestre',
  yearly: 'ano',
};

export const TREND_METRIC_OPTIONS: { value: MuscleGroupsMappedMetric; label: string }[] = [
  { value: 'sets', label: 'Séries' },
  { value: 'volume', label: 'Volume' },
  { value: 'reps', label: 'Repetições' },
  { value: 'load', label: 'Carga' },
];

export function isTrendPeriod(value: string | null): value is MuscleGroupsMappedPeriod {
  return TREND_PERIOD_OPTIONS.some((option) => option.value === value);
}

export function isTrendMetric(value: string | null): value is MuscleGroupsMappedMetric {
  return TREND_METRIC_OPTIONS.some((option) => option.value === value);
}

/** Query string de GET /analytics/muscle-groups-mapped-trend. */
export function buildMuscleGroupTrendParams(filters: MuscleGroupTrendFilters): URLSearchParams {
  return new URLSearchParams({
    period: filters.period,
    metric: filters.metric,
    days: filters.days,
    include_secondary: String(filters.includeSecondary),
  });
}

export function muscleGroupTrendPath(filters: MuscleGroupTrendFilters): string {
  return `/analytics/muscle-groups-mapped-trend?${buildMuscleGroupTrendParams(filters).toString()}`;
}

/**
 * Prefixo `muscle-groups-mapped-trend` é o que a associação de exercícios
 * (`ASSOCIATION_INVALIDATED_QUERY_KEYS`) invalida — não renomear sem ajustar lá.
 */
export function muscleGroupTrendQueryKey(filters: MuscleGroupTrendFilters) {
  return ['muscle-groups-mapped-trend', filters.period, filters.days, filters.metric, filters.includeSecondary] as const;
}

/** Total acumulado por grupo na janela. */
export function trendGroupTotals(rows: readonly MuscleGroupsMappedTrendRow[]): Map<string, number> {
  const totals = new Map<string, number>();
  for (const row of rows) {
    totals.set(row.group, (totals.get(row.group) || 0) + Number(row.value || 0));
  }
  return totals;
}

/** Grupos ordenados por pressão acumulada (desc), desempate alfabético. */
export function trendGroupOptions(totals: Map<string, number>): string[] {
  return Array.from(totals.keys()).sort(
    (a, b) => (totals.get(b) || 0) - (totals.get(a) || 0) || a.localeCompare(b, 'pt-BR'),
  );
}

export function trendPeriods(rows: readonly MuscleGroupsMappedTrendRow[]): string[] {
  return Array.from(new Set(rows.map((row) => row.period))).sort((a, b) => a.localeCompare(b));
}

export function trendValueIndex(rows: readonly MuscleGroupsMappedTrendRow[]): Map<string, number> {
  const index = new Map<string, number>();
  for (const row of rows) {
    index.set(`${row.period}::${row.group}`, Number(row.value || 0));
  }
  return index;
}

/** Pivot por período: um ponto por período, uma chave por grupo visível. */
export function pivotTrend(
  periods: readonly string[],
  index: Map<string, number>,
  visibleGroups: readonly string[],
): Record<string, string | number>[] {
  return periods.map((currentPeriod) => {
    const point: Record<string, string | number> = { period: currentPeriod };
    for (const group of visibleGroups) {
      point[group] = index.get(`${currentPeriod}::${group}`) || 0;
    }
    return point;
  });
}

export type PressureRow = {
  group: string;
  latest: number;
  previous: number;
  total: number;
  variation: number | null;
};

/** Ranking "pressão no período mais recente" dos grupos exibidos. */
export function pressureRows(
  periods: readonly string[],
  index: Map<string, number>,
  totals: Map<string, number>,
  visibleGroups: readonly string[],
): PressureRow[] {
  const latestPeriod = periods[periods.length - 1];
  const previousPeriod = periods[periods.length - 2];
  return visibleGroups
    .map((group) => {
      const latest = index.get(`${latestPeriod || ''}::${group}`) || 0;
      const previous = index.get(`${previousPeriod || ''}::${group}`) || 0;
      const total = totals.get(group) || 0;
      const variation = previous > 0 ? ((latest - previous) / previous) * 100 : null;
      return { group, latest, previous, total, variation };
    })
    .sort((a, b) => b.latest - a.latest || b.total - a.total);
}

export function formatTrendMetric(value: number, metric: MuscleGroupsMappedMetric): string {
  const n = Number(value);
  if (!Number.isFinite(n)) return '-';
  if (metric === 'load' || metric === 'volume') {
    return `${n.toLocaleString('pt-BR', { minimumFractionDigits: 0, maximumFractionDigits: 0 })} kg`;
  }
  return n.toLocaleString('pt-BR', { minimumFractionDigits: 0, maximumFractionDigits: 0 });
}
