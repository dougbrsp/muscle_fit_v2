import { apiFetch } from '@/lib/api';
import { setUpdatePayload } from '@/lib/exercise-set-edit';
import {
  buildStartedAt,
  type ActivityDetail,
  type ExerciseFormState,
  type MappingItem,
  type WorkoutHeaderForm,
} from '@/lib/workout-log-types';

/**
 * Gravação de um exercício lançado (tela de detalhe do treino, desktop e
 * celular): edição = `PUT /exercises/logs/{id}`; novo = cria o exercício e
 * depois cada série (se uma série falhar, o erro diz qual e o que fazer).
 * Extraída sem mudança de comportamento do `WorkoutDetailClient`.
 */
export async function saveExerciseLog(args: {
  activityId: number;
  activity: ActivityDetail | undefined;
  form: ExerciseFormState;
  editingExerciseId: number | null;
  selectedMap: MappingItem | undefined;
}) {
  const { activityId, activity, form, editingExerciseId, selectedMap } = args;
  if (!form.exercise_name) {
    throw new Error('Selecione um exercício.');
  }

  if (editingExerciseId) {
    return apiFetch(`/exercises/logs/${editingExerciseId}`, {
      method: 'PUT',
      body: JSON.stringify({
        local_date: activity?.local_date,
        started_at: activity?.started_at || null,
        original_name: form.exercise_name,
        mapping_id: selectedMap?.id ?? null,
        mapped_name: selectedMap?.amazfit_name || form.exercise_name,
        muscle_primary: selectedMap?.primary_group || null,
        muscle_secondary: selectedMap?.secondary_group || null,
        gym_id: activity?.gym_id ?? null,
        conducted_by: activity?.conducted_by ?? null,
        equipment_id: form.equipment_id ? Number(form.equipment_id) : null,
        workout_plan_exercise_id: form.workout_plan_exercise_id ? Number(form.workout_plan_exercise_id) : null,
        muscle_pain: form.muscle_pain,
        joint_pain: form.joint_pain,
        sets: form.sets.map(setUpdatePayload),
      }),
    });
  }

  const exerciseResult = await apiFetch<{ exercise_id: number }>(`/workout-sessions/${activityId}/exercises`, {
    method: 'POST',
    body: JSON.stringify({
      original_name: form.exercise_name,
      mapping_id: selectedMap?.id ?? null,
      equipment_id: form.equipment_id ? Number(form.equipment_id) : null,
      workout_plan_exercise_id: form.workout_plan_exercise_id ? Number(form.workout_plan_exercise_id) : null,
      muscle_pain: form.muscle_pain,
      joint_pain: form.joint_pain,
    }),
  });

  for (let index = 0; index < form.sets.length; index += 1) {
    const set = form.sets[index];
    try {
      await apiFetch(`/workout-sessions/${activityId}/exercises/${exerciseResult.exercise_id}/sets`, {
        method: 'POST',
        body: JSON.stringify({
          set_number: index + 1,
          reps: set.reps,
          weight_kg: set.weight_kg,
          rpe: set.rpe,
        }),
      });
    } catch (error) {
      throw new Error(
        `Exercício criado (série ${index + 1} de ${form.sets.length} falhou ao salvar). ` +
          `Edite o registro na lista abaixo para completar as séries restantes. Detalhe: ${
            error instanceof Error ? error.message : String(error)
          }`,
      );
    }
  }

  return exerciseResult;
}

/** Corpo do `PATCH /workout-sessions/{id}/meta` ao editar o cabeçalho do treino. */
export function headerMetaPayload(headerForm: WorkoutHeaderForm) {
  return {
    name: headerForm.name,
    local_date: headerForm.local_date,
    started_at: buildStartedAt(headerForm.local_date, headerForm.started_at_time),
    gym_id: headerForm.gym_id ? Number(headerForm.gym_id) : null,
    training_type_id: headerForm.training_type_id ? Number(headerForm.training_type_id) : null,
    conducted_by: headerForm.conducted_by || null,
    workout_plan_id: headerForm.workout_plan_id ? Number(headerForm.workout_plan_id) : null,
  };
}
