import { describe, expect, it } from 'vitest';
import {
  ALL_GOALS,
  defaultGoalValue,
  goalQueryParam,
  isHighlightedGoal,
  knownGoal,
  progressionGoalOptions,
  progressionGoalSelectValue,
  sessionGoalFilterOptions,
} from './training-goal-filter';
import type { TrainingGoalOption, TrainingGoalsResponse } from './training-goals';
import { dashboardKeys } from './dashboard-api';
import { slotComparisonPath } from './slot-comparison';

// Resposta de `GET /training-goals` (rótulos e default vêm da API; a tela não os escreve).
function opcao(value: string, label: string, isDefault: boolean): TrainingGoalOption {
  return { value, label, is_default: isDefault } as TrainingGoalOption;
}
const GOALS: TrainingGoalsResponse = {
  default: 'g-default',
  items: [opcao('g-default', 'Rótulo A', true), opcao('g-outro', 'Rótulo B', false)],
};

describe('filtro de objetivo (M97 F4) com a lista de /training-goals', () => {
  it('opções com os rótulos da API; a tabela ganha "Todos" primeiro', () => {
    expect(progressionGoalOptions(GOALS)).toEqual([
      { value: 'g-default', label: 'Rótulo A' },
      { value: 'g-outro', label: 'Rótulo B' },
    ]);
    expect(sessionGoalFilterOptions(GOALS)[0].value).toBe(ALL_GOALS);
    expect(sessionGoalFilterOptions(GOALS)).toHaveLength(3);
    expect(progressionGoalOptions(undefined)).toEqual([]);
  });

  it('"Todos"/desconhecido não manda goal; valor do vocabulário manda', () => {
    expect(goalQueryParam(GOALS, ALL_GOALS)).toBeNull();
    expect(goalQueryParam(GOALS, 'xpto')).toBeNull();
    expect(goalQueryParam(GOALS, 'g-outro')).toBe('g-outro');
    expect(goalQueryParam(undefined, 'g-outro')).toBeNull();
    expect(knownGoal(GOALS, ' G-OUTRO ')).toBe('g-outro');
  });

  it('seletor da progressão: sem escolha mostra o default da API', () => {
    expect(progressionGoalSelectValue(GOALS, null)).toBe('g-default');
    expect(progressionGoalSelectValue(GOALS, 'g-outro')).toBe('g-outro');
    expect(progressionGoalSelectValue(undefined, null)).toBeNull();
    expect(defaultGoalValue(GOALS)).toBe('g-default');
  });

  it('destaque: só objetivo diferente do default, e nada sem a lista', () => {
    expect(isHighlightedGoal('g-outro', 'g-default')).toBe(true);
    expect(isHighlightedGoal('g-default', 'g-default')).toBe(false);
    expect(isHighlightedGoal('g-outro', null)).toBe(false);
    expect(isHighlightedGoal(null, 'g-default')).toBe(false);
  });

  it('dashboardKeys levam o objetivo (sem ele, null no fim da chave)', () => {
    const range = { days: 30 };
    expect(dashboardKeys.progression(1, range)).toEqual(['dashboard', 'progression', 1, 30, null, null, null]);
    expect(dashboardKeys.progression(1, range, 'g-outro')).not.toEqual(dashboardKeys.progression(1, range));
    expect(dashboardKeys.progressionTrend(1, range, 'g-outro').at(-1)).toBe('g-outro');
    expect(dashboardKeys.progressionSuggestion(1)).toEqual(['dashboard', 'progression-suggestion', 1, null]);
    expect(dashboardKeys.progressionSuggestion(1, 'g-outro').at(-1)).toBe('g-outro');
    const window = { window: '90d' } as const;
    expect(dashboardKeys.exerciseProgress(5, window, 'g-outro').at(-1)).toBe('g-outro');
    expect(dashboardKeys.exerciseProgress(5, window).at(-1)).toBeNull();
    expect(dashboardKeys.strengthPanel(window)).toEqual(['dashboard', 'strength-panel', '90d', null, null]);
  });

  it('comparativo por slot: sem goal, a URL de sempre', () => {
    expect(slotComparisonPath(400)).toBe('/analytics/muscle-groups-slot-comparison?limit=400');
    expect(slotComparisonPath(400, 'g-outro')).toBe('/analytics/muscle-groups-slot-comparison?limit=400&goal=g-outro');
  });
});
