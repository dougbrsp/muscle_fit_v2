import { describe, expect, it } from 'vitest';
import {
  filterBySwapEligibility,
  indexSwapOptions,
  swapEligibilityFor,
  type ExerciseSwapOptionsResponse,
} from './exercise-swap-options';

const response: ExerciseSwapOptionsResponse = {
  plan_id: 10,
  editable: true,
  rows: [
    { workout_plan_exercise_id: 1, primary_group_id: 12, exercise_ids: [5, 6] },
    { workout_plan_exercise_id: 2, primary_group_id: null, exercise_ids: null },
  ],
};

const catalog = [
  { mappingId: 5, label: 'Supino reto' },
  { mappingId: 6, label: 'Supino inclinado' },
  { mappingId: 7, label: 'Puxada alta' },
  { mappingId: null, label: 'Sem vínculo' },
];

describe('swapEligibilityFor', () => {
  const index = indexSwapOptions(response);

  it('linha nova ou tabela sem linhas salvas fica livre', () => {
    expect(swapEligibilityFor(null, index).kind).toBe('free');
    expect(swapEligibilityFor(1, undefined).kind).toBe('free');
  });

  it('linha salva oferece só os ids que o servidor devolveu', () => {
    const labels = filterBySwapEligibility(catalog, swapEligibilityFor(1, index)).map((item) => item.label);
    expect(labels).toEqual(['Supino reto', 'Supino inclinado']);
  });

  it('linha sem restrição no servidor oferece o catálogo inteiro', () => {
    expect(filterBySwapEligibility(catalog, swapEligibilityFor(2, index))).toHaveLength(catalog.length);
  });

  it('carregando ou linha ausente da resposta não oferece nada', () => {
    expect(filterBySwapEligibility(catalog, swapEligibilityFor(1, 'loading'))).toEqual([]);
    expect(filterBySwapEligibility(catalog, swapEligibilityFor(99, index))).toEqual([]);
  });
});
