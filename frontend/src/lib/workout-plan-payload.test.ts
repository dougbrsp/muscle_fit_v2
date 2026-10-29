import { describe, expect, it } from 'vitest';
import { buildDraftRawPayload, toWorkoutPlanExerciseInput, toWorkoutPlanInput } from './workout-plan-payload';
import type { WorkoutPlanExercise } from './workout-plans-types';
import parity from './training-goal.parity.json';

const saved: WorkoutPlanExercise = {
  workout_plan_exercise_id: 77,
  sequence: 1,
  exercise_name: 'Supino reto',
  muscle_primary: 'Peitoral',
  sets_count: 4,
  reps_target: '8-10',
};

describe('payload de save da ficha', () => {
  it('ecoa o id da linha salva (trava do grupamento no servidor)', () => {
    expect(toWorkoutPlanExerciseInput(saved, 1).workout_plan_exercise_id).toBe(77);
  });

  it('linha nova vai sem id', () => {
    const { workout_plan_exercise_id: _omit, ...draft } = saved;
    expect(toWorkoutPlanExerciseInput(draft, 2).workout_plan_exercise_id).toBeNull();
  });

  it('ecoa o objetivo da linha como veio (M97 F3); ausente some do JSON', () => {
    const goal = parity.payload.items.find((item) => !item.is_default)!.value;
    expect(toWorkoutPlanExerciseInput({ ...saved, training_goal: goal }, 1).training_goal).toBe(goal);
    expect(JSON.parse(JSON.stringify(toWorkoutPlanExerciseInput(saved, 1)))).not.toHaveProperty('training_goal');
  });

  it('sempre monta a lista de exercícios (quem decide omitir é a tela)', () => {
    const payload = toWorkoutPlanInput({ name: 'A', plan_type: 'full_body', status: 'planned' }, [saved]);
    expect(payload.exercises).toHaveLength(1);
  });
});

// M98 C0: a ficha não tem coluna de cardio; o cardio do slot do Modelo vai no
// `raw_payload` do save, senão se perde (o `WorkoutPlanIn` descarta o resto).
describe('cardio do rascunho no raw_payload', () => {
  it('leva minutos e prescrições do rascunho', () => {
    const prescription = {
      cardio_mode: 'hiit',
      cardio_duration_minutes: null,
      cardio_warmup_minutes: 5,
      cardio_cooldown_minutes: 5,
      cardio_target_zone: null,
      hiit_low_seconds: 90,
      hiit_high_seconds: 30,
      hiit_low_zone: 2,
      hiit_high_zone: 4,
      hiit_rounds: 8,
      total_seconds: 1560,
      text: '5 min aquec. + 8× [30 s Z4 / 90 s Z2] + 5 min recuperação',
    };
    const raw = buildDraftRawPayload({
      name: 'A',
      plan_type: 'full_body',
      exercises: [],
      cardio_minutes: 26,
      cardio_prescriptions: [prescription],
    });
    expect(raw.cardio_minutes).toBe(26);
    expect(raw.cardio_prescriptions).toEqual([prescription]);
  });

  it('rascunho sem cardio (ou de API anterior) grava vazio', () => {
    const raw = buildDraftRawPayload({ name: 'A', plan_type: 'full_body', exercises: [] });
    expect(raw.cardio_minutes).toBeNull();
    expect(raw.cardio_prescriptions).toEqual([]);
  });
});
