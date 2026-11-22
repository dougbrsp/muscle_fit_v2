import { describe, expect, it } from 'vitest';
import { WINDOW_TREND_FALLBACK_TITLE, windowTrendHeading } from '@/lib/exercise-window-trend';

const SCOPE = {
  code: 'exercise_window_e1rm_trend',
  entity: 'exercise',
  metric: 'e1rm_mean',
  comparator: 'first_half_vs_second_half_of_window',
  label: 'Primeira metade vs. segunda da janela',
  description: 'Compara a média de e1RM da primeira metade com a da segunda.',
};

// Só a função pura: renderizar o componente inteiro exige muitos mocks (query, rota, filtros).
describe('windowTrendHeading', () => {
  it('escopo com rótulo e descrição: título é o rótulo, descrição vai para o tooltip', () => {
    expect(windowTrendHeading(SCOPE)).toEqual({ title: SCOPE.label, description: SCOPE.description });
  });

  it('sem escopo (servidor antigo): título de reserva, sem descrição', () => {
    expect(windowTrendHeading(null)).toEqual({ title: WINDOW_TREND_FALLBACK_TITLE, description: null });
    expect(windowTrendHeading(undefined).title).toBe(WINDOW_TREND_FALLBACK_TITLE);
  });

  it('rótulo vazio: título de reserva; descrição segue acessível', () => {
    expect(windowTrendHeading({ ...SCOPE, label: '' })).toEqual({
      title: WINDOW_TREND_FALLBACK_TITLE,
      description: SCOPE.description,
    });
  });
});
