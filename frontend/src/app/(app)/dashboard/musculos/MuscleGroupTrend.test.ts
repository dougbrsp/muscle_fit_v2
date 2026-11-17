import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import {
  DEFAULT_MUSCLE_GROUP_TREND_FILTERS,
  buildMuscleGroupTrendParams,
  muscleGroupTrendPath,
  muscleGroupTrendQueryKey,
  TREND_TONE_COLOR,
  formatTrendVariation,
  pivotTrend,
  pressureRows,
  trendGroupOptions,
  trendPeriods,
  trendValueIndex,
  type MuscleGroupTrendFilters,
} from '@/lib/muscle-group-trend';
import type {
  MuscleGroupsMappedPayload,
  MuscleGroupsMappedPressureRow,
  MuscleGroupsMappedTableRow,
  MuscleGroupsMappedTrendRow,
} from '@/lib/muscle-groups-types';
import { theme } from '@/theme';
import { MuscleGroupTrend } from './MuscleGroupTrend';

/**
 * Bloco "Tendência por grupo muscular" de /dashboard/musculos. Render via
 * `react-dom/server` (sem jsdom) com o cache do React Query pré-carregado;
 * os filtros são verificados pelos helpers puros que montam params e chave.
 */

function row(period: string, group: string, value: number): MuscleGroupsMappedTrendRow {
  return {
    period,
    group,
    muscle_group: group,
    value,
    sets: 1,
    volume_kg: value,
    avg_rpe: null,
    rpe_sets: 0,
    exercise_count: 1,
    exercises: [],
    source: 'mapped',
  };
}

const ROWS: MuscleGroupsMappedTrendRow[] = [
  row('2026-W38', 'Peitoral', 1000),
  row('2026-W39', 'Peitoral', 1200),
  row('2026-W38', 'Costas', 900),
  row('2026-W39', 'Costas', 600),
  row('2026-W39', 'Bíceps', 300),
];

function tableRow(group: string, value: number): MuscleGroupsMappedTableRow {
  const { period: _period, ...rest } = row('', group, value);
  return { ...rest, period_count: 1 };
}

// Totais e ranking como a API entrega para ROWS (prontos do backend).
const TABLE: MuscleGroupsMappedTableRow[] = [tableRow('Costas', 1500), tableRow('Peitoral', 2200), tableRow('Bíceps', 300)];
const PRESSURE: MuscleGroupsMappedPressureRow[] = [
  { group: 'Peitoral', latest: 1200, previous: 1000, total: 2200, variation_pct: 20, tone: 'up' },
  { group: 'Costas', latest: 600, previous: 900, total: 1500, variation_pct: -33.3, tone: 'down' },
  { group: 'Bíceps', latest: 300, previous: 0, total: 300, variation_pct: null, tone: null },
];

function payload(rows: MuscleGroupsMappedTrendRow[], extra: Partial<MuscleGroupsMappedPayload> = {}): MuscleGroupsMappedPayload {
  return {
    ok: true,
    source: 'mapped',
    metric: 'volume',
    period: 'weekly',
    days: 180,
    total_sets: rows.length,
    mapped_sets: rows.length,
    unmapped_sets: 0,
    mapped_ratio: 1,
    unmapped: [],
    trend_rows: rows,
    table_rows: [],
    ...extra,
  };
}

function render(filters: MuscleGroupTrendFilters, data?: MuscleGroupsMappedPayload): string {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  if (data) client.setQueryData(muscleGroupTrendQueryKey(filters), data);
  return renderToStaticMarkup(
    createElement(
      QueryClientProvider,
      { client },
      createElement(MantineProvider, { theme }, createElement(MuscleGroupTrend, { initialFilters: filters })),
    ),
  );
}

describe('params e chave da query', () => {
  it('default: semanal, volume, 180 dias, sem secundário', () => {
    const params = buildMuscleGroupTrendParams(DEFAULT_MUSCLE_GROUP_TREND_FILTERS);
    expect(Object.fromEntries(params)).toEqual({
      period: 'weekly',
      metric: 'volume',
      days: '180',
      include_secondary: 'false',
    });
    expect(muscleGroupTrendPath(DEFAULT_MUSCLE_GROUP_TREND_FILTERS)).toMatch(
      /^\/analytics\/muscle-groups-mapped-trend\?period=weekly&metric=volume&days=180&/,
    );
  });

  it('cada filtro muda o param correspondente e a chave da query', () => {
    const base = DEFAULT_MUSCLE_GROUP_TREND_FILTERS;
    const variants: [Partial<MuscleGroupTrendFilters>, string, string][] = [
      [{ period: 'monthly' }, 'period', 'monthly'],
      [{ metric: 'sets' }, 'metric', 'sets'],
      [{ days: '30' }, 'days', '30'],
      [{ includeSecondary: true }, 'include_secondary', 'true'],
    ];
    for (const [patch, key, expected] of variants) {
      const next = { ...base, ...patch };
      expect(buildMuscleGroupTrendParams(next).get(key)).toBe(expected);
      expect(muscleGroupTrendQueryKey(next)).not.toEqual(muscleGroupTrendQueryKey(base));
    }
  });

  it('mantém o prefixo invalidado pela associação de exercícios', () => {
    expect(muscleGroupTrendQueryKey(DEFAULT_MUSCLE_GROUP_TREND_FILTERS)).toEqual([
      'muscle-groups-mapped-trend',
      'weekly',
      '180',
      'volume',
      false,
    ]);
  });
});

describe('pivot e ranking', () => {
  const periods = trendPeriods(ROWS);
  const index = trendValueIndex(ROWS);

  it('ordena grupos pelo total pronto de table_rows (desc), empate por nome', () => {
    expect(trendGroupOptions(TABLE)).toEqual(['Peitoral', 'Costas', 'Bíceps']);
    const tie = [tableRow('Costas', 10), tableRow('Bíceps', 10), tableRow('Abdômen', 20)];
    expect(trendGroupOptions(tie)).toEqual(['Abdômen', 'Bíceps', 'Costas']);
    expect(trendGroupOptions([])).toEqual([]);
  });

  it('pivota um ponto por período com zero para grupo ausente', () => {
    expect(pivotTrend(periods, index, ['Peitoral', 'Bíceps'])).toEqual([
      { period: '2026-W38', Peitoral: 1000, Bíceps: 0 },
      { period: '2026-W39', Peitoral: 1200, Bíceps: 300 },
    ]);
  });

  it('ranking só filtra as pressure_rows da API, na ordem do servidor', () => {
    expect(pressureRows(PRESSURE, ['Bíceps', 'Peitoral']).map((r) => r.group)).toEqual(['Peitoral', 'Bíceps']);
    expect(pressureRows(PRESSURE, ['Peitoral', 'Costas', 'Bíceps'])).toEqual(PRESSURE);
  });

  it('API antiga sem pressure_rows: ranking vazio, sem cálculo local', () => {
    expect(pressureRows(undefined, ['Peitoral'])).toEqual([]);
  });

  it('tom da API vira a cor do badge; variação pronta formatada com sinal', () => {
    expect(TREND_TONE_COLOR).toEqual({ up: 'red', down: 'teal', stable: 'gray' });
    expect(formatTrendVariation(20)).toBe('+20.0%');
    expect(formatTrendVariation(-33.3)).toBe('-33.3%');
    expect(formatTrendVariation(0)).toBe('0.0%');
    expect(formatTrendVariation(null)).toBeNull();
  });
});

describe('<MuscleGroupTrend />', () => {
  it('renderiza título, filtros e ranking com dados no cache', () => {
    const html = render(
      DEFAULT_MUSCLE_GROUP_TREND_FILTERS,
      payload(ROWS, { table_rows: TABLE, pressure_rows: PRESSURE, latest_period: '2026-W39', previous_period: '2026-W38' }),
    );
    expect(html).toContain('Tendência por grupo muscular');
    expect(html).toContain('Granularidade');
    expect(html).toContain('Grupos exibidos');
    expect(html).toContain('Pressão no período mais recente');
    expect(html).toContain('2026-W39');
    expect(html).toContain('Peitoral');
    expect(html).toContain('+20.0%');
    expect(html).toContain('-33.3%');
    expect(html).toContain('Sem base');
  });

  it('API antiga sem pressure_rows: gráfico segue e o ranking mostra "sem dados"', () => {
    const html = render(DEFAULT_MUSCLE_GROUP_TREND_FILTERS, payload(ROWS, { table_rows: TABLE }));
    expect(html).toContain('Pressão no período mais recente');
    expect(html).toContain('Sem período');
    expect(html).toContain('Sem dados de pressão para os grupos exibidos.');
    expect(html).not.toContain('%</');
  });

  it('os filtros iniciais leem a entrada de cache da chave correspondente', () => {
    const monthly: MuscleGroupTrendFilters = { ...DEFAULT_MUSCLE_GROUP_TREND_FILTERS, period: 'monthly', metric: 'sets' };
    const html = render(
      monthly,
      payload([row('2026-09', 'Glúteos', 12)], {
        table_rows: [tableRow('Glúteos', 12)],
        pressure_rows: [{ group: 'Glúteos', latest: 12, previous: 0, total: 12, variation_pct: null, tone: null }],
        latest_period: '2026-09',
      }),
    );
    expect(html).toContain('Glúteos');
    expect(html).toContain('2026-09');
  });

  it('sem dados no cache mostra o carregamento, sem loop de render', () => {
    // Um setState derivado durante o render estouraria "Too many re-renders"
    // já no SSR; aqui a query fica pendente (opções vazias) e o render termina.
    const html = render(DEFAULT_MUSCLE_GROUP_TREND_FILTERS);
    expect(html).toContain('Carregando tendência por grupo');
  });

  it('resposta vazia mostra estado vazio', () => {
    const html = render(DEFAULT_MUSCLE_GROUP_TREND_FILTERS, payload([]));
    expect(html).toContain('Nenhum dado disponível para os filtros selecionados.');
  });

  it('seleção de grupos é derivada, nunca podada por efeito (regressão React #185, PR #290)', () => {
    const source = readFileSync(fileURLToPath(new URL('./MuscleGroupTrend.tsx', import.meta.url)), 'utf-8');
    expect(source).not.toMatch(/\buseEffect\b|\buseLayoutEffect\b/);
    expect(source).toContain('keepAvailableGroups(selectedGroups, groupOptions)');
  });
});
