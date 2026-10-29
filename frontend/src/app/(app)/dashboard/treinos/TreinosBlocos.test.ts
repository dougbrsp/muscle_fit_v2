import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement, type ReactElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { ApiError } from '@/lib/api';
import { dashboardKeys } from '@/lib/dashboard-api';
import { WEEKLY_SUMMARY_QUERY_DAYS } from '@/lib/dashboard-training-analytics';
import type { WorkoutsAnalysisGenerateResponse, WorkoutsAnalysisTraining } from '@/lib/workouts-analysis-types';
import { theme } from '@/theme';
import { AnaliseIaView } from './AnaliseIaBloco';
import { repsEmparelhadas } from './AnaliseIaSnapshot';
import { DistribuicaoRpeBloco } from './DistribuicaoRpeBloco';
import { EvolucaoSemanalBloco } from './EvolucaoSemanalBloco';
import { ResumoSemanalBloco } from './ResumoSemanalBloco';

/**
 * Blocos que `/dashboard/treinos` herdou de `/treinos`. Render via
 * `react-dom/server` (sem jsdom) com o cache do React Query pré-carregado.
 */

function render(element: ReactElement, seed: (client: QueryClient) => void = () => {}): string {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  seed(client);
  return renderToStaticMarkup(
    createElement(QueryClientProvider, { client }, createElement(MantineProvider, { theme }, element)),
  );
}

const TRAINING = {
  days: 28,
  weekly_summary: {
    completed_workouts: 3,
    planned_workouts: 4,
    total_sets: 60,
    total_volume_kg: 10000,
    avg_rpe: 8,
    high_rpe_sets: 5,
    latest_workouts: [],
    next_workout: null,
  },
  volume_overview: [],
  recent_records: [
    {
      exercise_id: 7,
      exercise_name: 'Supino reto',
      equipment_id: 9,
      equipment_name: 'Banco reto',
      load_convention: 'total',
      local_date: '2026-10-01',
      record_weight_kg: 82.5,
      previous_best_weight_kg: 80,
      reps: 6,
    },
  ],
  recovery: [],
  weekly_trend: [],
  weekly_trend_by_muscle_group: [],
  sessions: [],
} as unknown as WorkoutsAnalysisTraining;

describe('<ResumoSemanalBloco />', () => {
  it('mostra os quatro números da semana corrente', () => {
    const html = render(createElement(ResumoSemanalBloco), (client) =>
      client.setQueryData(dashboardKeys.trainingEvolution(WEEKLY_SUMMARY_QUERY_DAYS), TRAINING),
    );
    expect(html).toContain('Resumo da semana');
    expect(html).toContain('Treinos realizados');
    expect(html).toContain('Previstos: 4');
    expect(html).toContain('10.000 kg');
  });

  it('sem dados no cache mostra o esqueleto', () => {
    expect(render(createElement(ResumoSemanalBloco))).toContain('aria-busy');
  });
});

describe('<EvolucaoSemanalBloco /> e <DistribuicaoRpeBloco />', () => {
  it('resposta vazia vira estado vazio, com a janela do seletor na legenda', () => {
    const html = render(createElement(EvolucaoSemanalBloco, { days: 90 }), (client) =>
      client.setQueryData(dashboardKeys.trainingTrend(90), { period: 'week', days: 90, items: [] }),
    );
    expect(html).toContain('Evolução por semana ISO');
    expect(html).toContain('últimos 90 dias');
    expect(html).toContain('Sem treinos registrados no período selecionado.');
  });

  it('RPE soma as séries na legenda', () => {
    const html = render(createElement(DistribuicaoRpeBloco, { days: 28 }), (client) =>
      client.setQueryData(dashboardKeys.rpeDistribution(28), {
        days: 28,
        items: [
          { rpe: 8, sets_count: 10 },
          { rpe: 9, sets_count: 5 },
        ],
      }),
    );
    expect(html).toContain('Distribuição de RPE');
    expect(html).toContain('15 série(s) com RPE nos últimos 28 dias');
  });
});

describe('<AnaliseIaView />', () => {
  const base = { days: 28, pending: false, error: null, result: null, resultDays: null, onGenerate: () => {} };

  it('estado inicial: só o botão, sem análise', () => {
    const html = render(createElement(AnaliseIaView, base));
    expect(html).toContain('Análise com personal trainer IA');
    expect(html).toContain('Gerar análise');
    expect(html).not.toContain('Dados usados pela IA');
  });

  it('gerando: avisa que o job roda em segundo plano', () => {
    expect(render(createElement(AnaliseIaView, { ...base, pending: true }))).toContain('segundo plano');
  });

  it('503 mostra IA indisponível', () => {
    const html = render(createElement(AnaliseIaView, { ...base, error: new ApiError('x', 503) }));
    expect(html).toContain('IA indisponível no momento');
  });

  it('resultado: markdown + snapshot; avisa quando a janela mudou', () => {
    const result: WorkoutsAnalysisGenerateResponse = {
      model: 'm',
      insight: '## Resumo\n\nBoa **consistência**.',
      training: TRAINING,
    };
    const html = render(createElement(AnaliseIaView, { ...base, result, resultDays: 90 }));
    expect(html).toContain('Resumo');
    expect(html).toContain('consistência');
    expect(html).toContain('Dados usados pela IA');
    // Carga máxima não é chamada de "progressão" (M74).
    expect(html).not.toContain('Destaques de progressão');
    // M76 Lote 3b: recordes pela regra única (chave exercício + equipamento).
    expect(html).not.toContain('Maior carga registrada');
    expect(html).toContain('Recordes de carga (60 dias)');
    expect(html).toContain('Supino reto');
    expect(html).toContain('Banco reto');
    expect(html).toContain('gerada para os últimos 90 dias');
  });

  it('snapshot mostra o status da regra única por exercício', () => {
    const training: WorkoutsAnalysisTraining = {
      ...TRAINING,
      exercise_progression: [
        {
          exercise_id: 1001,
          exercise_name: 'Agachamento livre',
          status: 'PROGRESSING',
          status_label: 'Progredindo',
          status_reasons: ['same_load_more_reps'],
          comparable_exposures: 2,
          exposures_in_window: 2,
          equipment_id: 11,
          equipment_name: 'Gaiola',
          load_convention: 'total',
          latest: {
            local_date: '2026-10-06', best_load_kg: 40, top_set_reps: [14, 13], working_sets: 2,
            avg_rpe: 9.25, e1rm: 59.3, e1rm_reps: 14, e1rm_reliable: false,
          },
          previous_comparable: {
            local_date: '2026-10-02', best_load_kg: 40, top_set_reps: [11, 10, 10], working_sets: 3,
            avg_rpe: 9, e1rm: 54.7, e1rm_reps: 11, e1rm_reliable: true,
          },
          paired_top_reps: { pairs: 2, previous: [11, 10], current: [14, 13], previous_sum: 21, current_sum: 27, delta: 6 },
        },
      ],
    };
    const result: WorkoutsAnalysisGenerateResponse = { model: 'm', insight: 'ok', training };
    const html = render(createElement(AnaliseIaView, { ...base, result, resultDays: 28 }));
    expect(html).toContain('Status de progressão por exercício');
    expect(html).toContain('Agachamento livre');
    expect(html).toContain('Gaiola');
    expect(html).toContain('11+10 → 14+13');
    expect(html).not.toContain('reps + RIR');
  });

  it('reps emparelhadas ajustadas pelo esforço (M78) vêm rotuladas como reps + RIR', () => {
    expect(
      repsEmparelhadas({
        pairs: 2,
        previous: [12, 11],
        current: [12, 11],
        previous_sum: 23,
        current_sum: 23,
        delta: 0,
        effort_adjusted: true,
        previous_reps: [12, 11],
        current_reps: [11, 11],
      }),
    ).toBe('12+11 → 12+11 (reps + RIR)');
  });
});
