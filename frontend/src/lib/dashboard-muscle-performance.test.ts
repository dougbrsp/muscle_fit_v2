import { describe, expect, it } from 'vitest';
import { OUTLIER_LABEL, performanceTooltip } from './dashboard-muscle-performance';
import type { MuscleGroupPerformanceExercise, MuscleGroupProgressionItem } from './dashboard-v2-types';

function ex(over: Partial<MuscleGroupPerformanceExercise>): MuscleGroupPerformanceExercise {
  return {
    exercise_id: 1,
    exercise_name: 'Puxada',
    equipment_id: 5,
    load_convention: 'total',
    previous_e1rm_kg: 100,
    current_e1rm_kg: 106,
    change_pct: 6,
    stimulus_weight: 1,
    outlier: false,
    excluded_reason: null,
    ...over,
  };
}

function item(over: Partial<MuscleGroupProgressionItem>): MuscleGroupProgressionItem {
  return {
    performance_status: 'PROGREDINDO',
    performance_change_pct: 6,
    performance_exercises_compared: 1,
    performance_exercises_excluded: 0,
    performance_activation_sum: 1,
    performance_insufficient_reason: null,
    performance_exercises: [ex({})],
    ...over,
  } as MuscleGroupProgressionItem;
}

describe('performanceTooltip', () => {
  it('marca o exercício atípico para conferir o registro', () => {
    const text = performanceTooltip(
      item({
        performance_exercises_excluded: 1,
        performance_exercises: [
          ex({}),
          ex({
            exercise_name: 'Remada articulada',
            previous_e1rm_kg: 46,
            current_e1rm_kg: 101,
            change_pct: 119.6,
            outlier: true,
            excluded_reason: 'OUTLIER_CHANGE',
          }),
        ],
      }),
    );
    const [header, used, outlier] = (text ?? '').split('\n');
    expect(header).toContain('(1 exercícios)');
    expect(used).not.toContain(OUTLIER_LABEL);
    expect(outlier).toContain('Remada articulada');
    expect(outlier).toContain(OUTLIER_LABEL);
  });

  it('explica ativação baixa', () => {
    const text = performanceTooltip(
      item({
        performance_status: 'DADOS_INSUFICIENTES',
        performance_change_pct: null,
        performance_insufficient_reason: 'LOW_ACTIVATION',
        performance_exercises: [ex({ stimulus_weight: 0.2 })],
      }),
    );
    expect(text).toContain('Poucos exercícios trabalham este grupo diretamente');
    expect(text).toContain('ativação 20%');
  });

  it('explica quando todos são atípicos', () => {
    const text = performanceTooltip(
      item({
        performance_status: 'DADOS_INSUFICIENTES',
        performance_change_pct: null,
        performance_insufficient_reason: 'ALL_OUTLIERS',
        performance_exercises: [ex({ outlier: true, excluded_reason: 'OUTLIER_CHANGE', change_pct: 80 })],
      }),
    );
    expect(text).toContain('Todos os exercícios');
    expect(text).toContain(OUTLIER_LABEL);
  });

  it('sem exercício comparável não tem tooltip', () => {
    const text = performanceTooltip(
      item({
        performance_status: 'DADOS_INSUFICIENTES',
        performance_change_pct: null,
        performance_insufficient_reason: 'NO_COMPARABLE_EXERCISE',
        performance_exercises: [],
      }),
    );
    expect(text).toBeNull();
  });
});
