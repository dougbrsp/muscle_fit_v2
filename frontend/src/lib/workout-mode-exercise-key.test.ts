import { describe, expect, it } from 'vitest';
import { exerciseKeySlot } from './workout-mode-exercise-key';

const base = { sessionId: 7, exerciseName: 'Supino' };

describe('exerciseKeySlot', () => {
  it('item real mantém sessão#idDoItem', () => {
    expect(exerciseKeySlot({ ...base, planItemId: 12, originalPlanItemId: 12 })).toBe('7#12');
  });

  it('treino sem item usa o nome', () => {
    expect(exerciseKeySlot({ ...base, planItemId: null, originalPlanItemId: null })).toBe('7#Supino');
  });

  it('continuação difere do original e usa o id sintético', () => {
    const original = exerciseKeySlot({ ...base, planItemId: 12, originalPlanItemId: 12 });
    const cont = exerciseKeySlot({ ...base, planItemId: -1, originalPlanItemId: 12 });
    expect(cont).toBe('7#-1');
    expect(cont).not.toBe(original);
  });

  it('duas continuações do mesmo item diferem e a mesma é estável', () => {
    const a = exerciseKeySlot({ ...base, planItemId: -1, originalPlanItemId: 12 });
    const b = exerciseKeySlot({ ...base, planItemId: -2, originalPlanItemId: 12 });
    expect(a).not.toBe(b);
    expect(exerciseKeySlot({ ...base, planItemId: -1, originalPlanItemId: 12 })).toBe(a);
  });
});
