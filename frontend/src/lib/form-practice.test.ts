import { describe, expect, it } from 'vitest';
import {
  FORM_PRACTICE_LABEL,
  exerciseFormPracticeField,
  formPracticeCreateField,
  formPracticeStateFromSetResponse,
  removeFormPracticePending,
  reconcileMarkAfterResponse,
  isFormPracticePending,
  isFormPracticeRow,
  nextServerStateOnCreated,
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

describe('form-practice: nextServerStateOnCreated', () => {
  it('mesmo exercício já travado mantém o estado', () => {
    const prev = { exerciseId: 7, marked: true, locked: true };
    expect(nextServerStateOnCreated(prev, 7, false)).toBe(prev);
  });
  it('id diferente ou sem estado grava o inicial destravado', () => {
    expect(nextServerStateOnCreated({ exerciseId: 7, marked: true, locked: true }, 8, true)).toEqual({
      exerciseId: 8,
      marked: true,
      locked: false,
    });
    expect(nextServerStateOnCreated(undefined, 9, false)).toEqual({ exerciseId: 9, marked: false, locked: false });
  });
});

describe('form-practice (#213): corpos e estado do servidor', () => {
  it('criação do exercício envia form_practice explícito, inclusive false', () => {
    expect(formPracticeCreateField(true)).toEqual({ form_practice: true });
    expect(formPracticeCreateField(false)).toEqual({ form_practice: false });
  });

  it('POST de série leva exercise_form_practice só enquanto não travado', () => {
    expect(exerciseFormPracticeField({ marked: true, locked: false })).toEqual({ exercise_form_practice: true });
    expect(exerciseFormPracticeField({ marked: false, locked: false })).toEqual({ exercise_form_practice: false });
    expect(exerciseFormPracticeField({ marked: true, locked: true })).toEqual({});
  });

  it('lê form_practice/form_practice_locked da resposta de série', () => {
    expect(formPracticeStateFromSetResponse({ form_practice: false, form_practice_locked: false })).toEqual({ marked: false, locked: false });
    expect(formPracticeStateFromSetResponse({ form_practice: true, form_practice_locked: true })).toEqual({ marked: true, locked: true });
    expect(formPracticeStateFromSetResponse({})).toBeNull();
    expect(formPracticeStateFromSetResponse(null)).toBeNull();
  });

  it('limpa a pendência do item sem mutar a lista', () => {
    const ids = [3, 7];
    expect(removeFormPracticePending(ids, 7)).toEqual([3]);
    expect(removeFormPracticePending(ids, null)).toEqual([3, 7]);
    expect(ids).toEqual([3, 7]);
  });
});

describe('form-practice (#213): toque durante o envio da série', () => {
  const sent = { marked: false, locked: false };
  it('preserva a escolha mais recente quando o servidor não travou', () => {
    expect(reconcileMarkAfterResponse(sent, { marked: true, locked: false }, { marked: false, locked: false })).toEqual({
      marked: true,
      locked: false,
    });
  });
  it('servidor travado vale o servidor', () => {
    expect(reconcileMarkAfterResponse(sent, { marked: true, locked: false }, { marked: false, locked: true })).toEqual({
      marked: false,
      locked: true,
    });
  });
  it('sem toque no intervalo vale o servidor', () => {
    expect(reconcileMarkAfterResponse(sent, sent, { marked: false, locked: false })).toEqual(sent);
  });
});
