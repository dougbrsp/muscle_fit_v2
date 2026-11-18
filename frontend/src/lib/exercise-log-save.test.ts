import { beforeEach, describe, expect, it, vi } from 'vitest';

import { emptyExerciseForm, saveExerciseLog } from './exercise-log-save';

vi.mock('./api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./api')>();
  return { ...actual, apiFetch: vi.fn() };
});

const { apiFetch } = await import('./api');
const apiFetchMock = vi.mocked(apiFetch);

function form(formPractice: boolean) {
  return { ...emptyExerciseForm(), exercise_name: 'Supino reto', form_practice: formPractice };
}

function bodyOf(callIndex: number) {
  return JSON.parse(apiFetchMock.mock.calls[callIndex][1]?.body as string);
}

describe('saveExerciseLog envia a marca de aprimoramento de técnica', () => {
  beforeEach(() => {
    apiFetchMock.mockReset();
    apiFetchMock.mockResolvedValue({ exercise_id: 9 });
  });

  it('formulário vazio começa sem a marca', () => {
    expect(emptyExerciseForm().form_practice).toBe(false);
  });

  it.each([true, false])('PUT de edição leva form_practice=%s', async (value) => {
    await saveExerciseLog({
      activityId: 1,
      activity: undefined,
      form: form(value),
      editingExerciseId: 7,
      selectedMap: undefined,
    });
    expect(apiFetchMock.mock.calls[0][0]).toBe('/exercises/logs/7');
    expect(bodyOf(0).form_practice).toBe(value);
  });

  it.each([true, false])('POST de criação leva form_practice=%s', async (value) => {
    await saveExerciseLog({
      activityId: 1,
      activity: undefined,
      form: form(value),
      editingExerciseId: null,
      selectedMap: undefined,
    });
    expect(apiFetchMock.mock.calls[0][0]).toBe('/workout-sessions/1/exercises');
    expect(bodyOf(0).form_practice).toBe(value);
  });
});
