import { describe, expect, it } from 'vitest';
import {
  FORM_PRACTICE_LABEL,
  isFormPracticePending,
  isFormPracticeRow,
  resolveFormPracticePlanExerciseId,
  toggleFormPracticePending,
} from './form-practice';
import type { MidExerciseExtra, StoredWorkoutModeSession } from './workout-mode-types';

const base: StoredWorkoutModeSession = { session_id: 1, source: 'plan' };

describe('form-practice', () => {
  it('rótulo de produto', () => {
    expect(FORM_PRACTICE_LABEL).toBe('Aprimoramento de técnica');
  });

  it('lê e alterna a marcação pendente sem mutar a sessão', () => {
    expect(isFormPracticePending(base, 7)).toBe(false);
    expect(isFormPracticePending(null, 7)).toBe(false);
    const on = toggleFormPracticePending(base, 7);
    expect(isFormPracticePending(on, 7)).toBe(true);
    expect(isFormPracticePending(on, 8)).toBe(false);
    expect(base.form_practice_plan_exercise_ids).toBeUndefined();
    const off = toggleFormPracticePending(on, 7);
    expect(isFormPracticePending(off, 7)).toBe(false);
    expect(on.form_practice_plan_exercise_ids).toEqual([7]);
  });

  it('continuação resolve para o id original; item normal fica como está', () => {
    const extras = [{ localId: -3, originalWorkoutPlanExerciseId: 7 }, { localId: -4, originalWorkoutPlanExerciseId: null }] as MidExerciseExtra[];
    expect(resolveFormPracticePlanExerciseId(-3, extras)).toBe(7);
    expect(resolveFormPracticePlanExerciseId(-4, extras)).toBeNull();
    expect(resolveFormPracticePlanExerciseId(9, extras)).toBe(9);
    expect(resolveFormPracticePlanExerciseId(9, undefined)).toBe(9);
  });

  it('linha da API: ausente/null = false', () => {
    expect(isFormPracticeRow({})).toBe(false);
    expect(isFormPracticeRow({ form_practice: null })).toBe(false);
    expect(isFormPracticeRow(undefined)).toBe(false);
    expect(isFormPracticeRow({ form_practice: true })).toBe(true);
  });
});
