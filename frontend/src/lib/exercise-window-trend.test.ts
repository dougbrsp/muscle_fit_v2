import { describe, expect, it } from 'vitest';
import type { WindowTrend } from './dashboard-v3-types';
import { buildWindowTrendView } from './exercise-window-trend';

const SCOPE = {
  code: 'exercise_window_e1rm_trend',
  entity: 'exercise',
  metric: 'e1rm_mean',
  comparator: 'first_half_vs_second_half_of_window',
  label: 'Tendência da janela',
  description: 'Compara a média de e1RM da primeira metade com a da segunda.',
};

const COM_NUMEROS: WindowTrend = {
  status: 'PROGRESSING',
  label: 'Progredindo',
  reasons: ['e1rm_up'],
  change_pct: 0.125,
  initial_mean: 53.33,
  final_mean: 66.67,
  exposures_used: 6,
  rule_version: 'exercise-window-trend-v1',
  scope: SCOPE,
};

describe('buildWindowTrendView', () => {
  it('formata variação e médias recebidas, sem decidir nada', () => {
    const view = buildWindowTrendView(COM_NUMEROS);
    expect(view).toEqual({
      statusCode: 'PROGRESSING',
      reasons: ['e1rm_up'],
      scope: SCOPE,
      changeText: '+12,5%',
      meansText: '53,3 kg → 66,7 kg',
      ruleVersion: 'exercise-window-trend-v1',
    });
  });

  it('dados insuficientes: selo e motivo, sem números', () => {
    const view = buildWindowTrendView({
      ...COM_NUMEROS,
      status: 'INSUFFICIENT_DATA',
      reasons: ['not_enough_exposures'],
      change_pct: null,
      initial_mean: null,
      final_mean: null,
      exposures_used: 1,
    });
    expect(view?.statusCode).toBe('INSUFFICIENT_DATA');
    expect(view?.reasons).toEqual(['not_enough_exposures']);
    expect(view?.changeText).toBeNull();
    expect(view?.meansText).toBeNull();
  });

  it('nula ou ausente (servidor antigo): sem seção', () => {
    expect(buildWindowTrendView(null)).toBeNull();
    expect(buildWindowTrendView(undefined)).toBeNull();
  });
});
