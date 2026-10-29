import { describe, expect, it } from 'vitest';

import {
  collectPendingRepsSets,
  finishNeedsPendingConfirmation,
  pendingRepsKey,
  pendingSetLabels,
} from './workout-mode-pending-reps';

describe('séries com reps pendentes ao finalizar o treino', () => {
  const exercises = [
    {
      id: 11,
      original_name: 'peck deck',
      exercise_name: 'Crucifixo na máquina',
      sets: [
        { set_number: 4, set_type: 'working', reps: null, reps_pending: 1, weight_kg: '45.00' },
        { set_number: 3, set_type: 'working', reps: 12, reps_pending: 0, weight_kg: '45.00' },
        { set_number: 5, set_type: 'drop_set', reps: null, reps_pending: true, weight_kg: null },
      ],
    },
    { id: 12, original_name: 'Supino', sets: [{ set_number: 1, set_type: 'working', reps: 10, weight_kg: 60 }] },
  ];

  it('lista só as pendentes, na ordem das séries, com o que o PATCH precisa', () => {
    const pending = collectPendingRepsSets(exercises);
    expect(pending).toEqual([
      { exerciseId: 11, exerciseName: 'Crucifixo na máquina', setNumber: 4, setType: 'working', weightKg: 45, label: 'série 2' },
      { exerciseId: 11, exerciseName: 'Crucifixo na máquina', setNumber: 5, setType: 'drop_set', weightKg: null, label: 'série 2 · drop 1' },
    ]);
    expect(pendingRepsKey(pending[0])).toBe('11#4');
  });

  it('com pendentes a finalização pede confirmação; sem elas, finaliza direto', () => {
    expect(finishNeedsPendingConfirmation(collectPendingRepsSets(exercises))).toBe(true);
    expect(finishNeedsPendingConfirmation(collectPendingRepsSets([exercises[1]]))).toBe(false);
    expect(collectPendingRepsSets(null)).toEqual([]);
  });

  it('série com reps NULL sem a marca (registro manual antigo) não é pendente', () => {
    const legacy = [{ id: 1, original_name: 'X', sets: [{ set_number: 1, reps: null, weight_kg: 10 }] }];
    expect(collectPendingRepsSets(legacy)).toEqual([]);
  });
});

describe('rótulo da pendente pela âncora e etapa', () => {
  it('mini-bloco 2 da série 2 não vira "série 4"', () => {
    const labels = pendingSetLabels([
      { set_number: 1, set_type: 'warmup' },
      { set_number: 2, set_type: 'working' },
      { set_number: 3, set_type: 'working' },
      { set_number: 4, set_type: 'rest_pause', anchor_set_number: 3, technique_code: 'rest_pause' },
      { set_number: 5, set_type: 'rest_pause', anchor_set_number: 3, technique_code: 'rest_pause' },
      { set_number: 6, set_type: 'forced_rep', anchor_set_number: 2, technique_code: 'forced_reps' },
    ]);
    expect(labels.get(3)).toBe('série 2');
    expect(labels.get(4)).toBe('série 2 · mini-bloco 1');
    expect(labels.get(5)).toBe('série 2 · mini-bloco 2');
    expect(labels.get(6)).toBe('série 1 · repetição extra 1');
  });
});
