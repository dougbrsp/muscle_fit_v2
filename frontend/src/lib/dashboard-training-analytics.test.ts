import { describe, expect, it } from 'vitest';
import { ApiError } from './api';
import {
  aiAnalysisFailure,
  hasAiSnapshot,
  isoWeekRows,
  rpeRows,
  totalRpeSets,
  weeklySummaryTiles,
} from './dashboard-training-analytics';
import type { WorkoutsAnalysisTraining } from './workouts-analysis-types';

describe('weeklySummaryTiles', () => {
  it('monta os quatro números na ordem da tela, com ausência como travessão', () => {
    const tiles = weeklySummaryTiles({
      completed_workouts: 3,
      planned_workouts: 4,
      total_sets: 62,
      total_volume_kg: 12480.4,
      avg_rpe: null,
      high_rpe_sets: 9,
      latest_workouts: [],
      next_workout: null,
    });
    expect(tiles.map((t) => t.key)).toEqual(['workouts', 'sets', 'volume', 'high_rpe']);
    expect(tiles[0]).toMatchObject({ value: '3', hint: 'Previstos: 4' });
    expect(tiles[2].value).toBe('12.480 kg');
    expect(tiles[2].hint).toBe('RPE médio: —');
    expect(tiles[3].value).toBe('9');
  });
});

describe('isoWeekRows', () => {
  it('volume ausente vira 0 e RPE ausente continua null', () => {
    const rows = isoWeekRows([
      { period: '2026-W39', period_start: '', period_end: '', workouts: 1, sets_count: 1, reps_total: 1, volume_kg: 0, avg_rpe: null, high_rpe_sets: 0 },
      { period: '2026-W40', period_start: '', period_end: '', workouts: 2, sets_count: 2, reps_total: 2, volume_kg: 1500, avg_rpe: 8.2, high_rpe_sets: 1 },
    ]);
    expect(rows).toEqual([
      { periodo: '2026-W39', volume: 0, rpe: null },
      { periodo: '2026-W40', volume: 1500, rpe: 8.2 },
    ]);
  });

  it('sem resposta devolve lista vazia', () => {
    expect(isoWeekRows(undefined)).toEqual([]);
  });
});

describe('rpeRows / totalRpeSets', () => {
  it('ordena por nota, formata em pt-BR e soma as séries', () => {
    const rows = rpeRows([
      { rpe: 9, sets_count: 4 },
      { rpe: 7.5, sets_count: 10 },
    ]);
    expect(rows).toEqual([
      { rpe: '7,5', sets: 10 },
      { rpe: '9', sets: 4 },
    ]);
    expect(totalRpeSets(rows)).toBe(14);
  });
});

describe('aiAnalysisFailure', () => {
  it('503 é IA indisponível', () => {
    expect(aiAnalysisFailure(new ApiError('x', 503)).title).toBe('IA indisponível no momento');
  });

  it('outros erros trazem a mensagem real', () => {
    const falha = aiAnalysisFailure(new Error('Tempo esgotado aguardando o job.'));
    expect(falha.title).toBe('Não foi possível gerar a análise por IA');
    expect(falha.detail).toBe('Tempo esgotado aguardando o job.');
  });

  it('erro sem mensagem usa o texto padrão', () => {
    expect(aiAnalysisFailure('falhou').detail).toBe('Não foi possível gerar a análise.');
  });
});

describe('hasAiSnapshot', () => {
  const vazio = {
    weekly_trend: [],
    volume_overview: [],
    recent_records: [],
    recovery: [],
  } as unknown as WorkoutsAnalysisTraining;

  it('falso sem training ou com todas as tabelas vazias', () => {
    expect(hasAiSnapshot(null)).toBe(false);
    expect(hasAiSnapshot(vazio)).toBe(false);
  });

  it('verdadeiro quando alguma tabela tem linha', () => {
    expect(
      hasAiSnapshot({
        ...vazio,
        recent_records: [
          {
            exercise_id: 1,
            exercise_name: 'Supino',
            equipment_id: 9,
            equipment_name: 'Banco reto',
            load_convention: 'total',
            local_date: '2026-10-01',
            record_weight_kg: 80,
            previous_best_weight_kg: 77.5,
            reps: 8,
          },
        ],
      }),
    ).toBe(true);
  });
});
