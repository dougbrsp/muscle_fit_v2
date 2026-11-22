import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from './api';
import { emptyExerciseForm, exerciseLogSaveErrorMessage, patchSet, saveExerciseLog, trainingGoalPayload } from './exercise-log-save';

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

  it('treino encerrado: exercício novo vai por POST /exercises/logs com activity_id', async () => {
    await saveExerciseLog({
      activityId: 20,
      activity: { id: 20, local_date: '2026-11-10', session_status: 'done' } as never,
      form: form(false),
      editingExerciseId: null,
      selectedMap: undefined,
    });
    expect(apiFetchMock).toHaveBeenCalledTimes(1);
    expect(apiFetchMock.mock.calls[0][0]).toBe('/exercises/logs');
    expect(apiFetchMock.mock.calls[0][1]?.method).toBe('POST');
    expect(bodyOf(0)).toMatchObject({ activity_id: 20, local_date: '2026-11-10', sets: [{ set_number: 1, source_set_number: null }] });
  });

  it('treino em andamento: exercício novo segue nas rotas da sessão', async () => {
    await saveExerciseLog({
      activityId: 20,
      activity: { id: 20, local_date: '2026-11-10', session_status: 'in_progress' } as never,
      form: form(false),
      editingExerciseId: null,
      selectedMap: undefined,
    });
    expect(apiFetchMock.mock.calls[0][0]).toBe('/workout-sessions/20/exercises');
  });

  it('limpar a dor envia null explícito no PUT', async () => {
    await saveExerciseLog({
      activityId: 1,
      activity: undefined,
      form: { ...form(false), muscle_pain: null },
      editingExerciseId: 7,
      selectedMap: undefined,
    });
    expect(bodyOf(0)).toHaveProperty('muscle_pain', null);
  });
});

describe('#219 objetivo, dor por série e erro 422', () => {
  beforeEach(() => {
    apiFetchMock.mockReset();
    apiFetchMock.mockResolvedValue({ exercise_id: 9 });
  });

  const save = (f: ReturnType<typeof emptyExerciseForm>) =>
    saveExerciseLog({ activityId: 1, activity: undefined, form: f, editingExerciseId: 7, selectedMap: undefined });

  it('training_goal só vai quando o usuário mudou o valor', async () => {
    await save({ ...form(false), training_goal: 'hypertrophy', initial_training_goal: 'hypertrophy' });
    expect('training_goal' in bodyOf(0)).toBe(false);
    await save({ ...form(false), training_goal: 'strength', initial_training_goal: 'hypertrophy' });
    expect(bodyOf(1).training_goal).toBe('strength');
    expect(trainingGoalPayload({ training_goal: null, initial_training_goal: 'strength' })).toEqual({});
  });

  it('PUT leva tipo, medida e dor por série', async () => {
    const f = form(false);
    f.sets = patchSet(f.sets, 0, (s) => ({ ...s, set_type: 'warmup', measure: 'duration', duration_seconds: 30, reps: null, muscle_pain: 4, joint_pain: true }));
    await save(f);
    expect(bodyOf(0).sets[0]).toMatchObject({ set_type: 'warmup', measure: 'duration', duration_seconds: 30, muscle_pain: 4, joint_pain: true });
  });

  it('usa a message do 422 do servidor', () => {
    const detail = { code: 'set_type_locked', set_number: 2, message: 'Série de técnica não troca de tipo.' };
    expect(exerciseLogSaveErrorMessage(new ApiError('Erro', 422, detail))).toBe(detail.message);
    expect(exerciseLogSaveErrorMessage(new Error('x'))).toBe('x');
  });
});
