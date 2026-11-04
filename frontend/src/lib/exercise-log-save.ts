import { apiFetch } from '@/lib/api';
import { newMeasuredSetFromLast, setEditFields, setUpdatePayload } from '@/lib/exercise-set-edit';
import type { SideField } from '@/lib/set-sides';
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

/** Série inicial de um exercício novo (defaults de apresentação do formulário; o servidor valida). */
export function defaultNewSet(setNumber = 1) {
  return { set_number: setNumber, reps: 10, weight_kg: 20, rpe: null };
}

export function emptyExerciseForm(): ExerciseFormState {
  return {
    exercise_name: '',
    equipment_id: '',
    muscle_group: null,
    workout_plan_exercise_id: '',
    muscle_pain: null,
    joint_pain: false,
    sets: [defaultNewSet()],
  };
}

type SetList = ExerciseFormState['sets'];

/** Nova série copiando a última (ou o padrão). */
export function appendSet(sets: SetList): SetList {
  return [...sets, newMeasuredSetFromLast(sets[sets.length - 1], sets.length + 1) ?? defaultNewSet(sets.length + 1)];
}

/** Remove a série e renumera; nunca deixa o formulário sem série. */
export function dropSet(sets: SetList, index: number): SetList {
  if (sets.length <= 1) return sets;
  return sets.filter((_, i) => i !== index).map((set, i) => ({ ...set, set_number: i + 1 }));
}

/** Edita um campo de uma série (lado vazio fica vazio; máximo de reps vazio fica vazio; demais: número ou 0). */
export function updateSetField(
  sets: SetList,
  index: number,
  field: 'reps' | 'weight_kg' | 'rpe' | 'duration_seconds' | SideField,
  value: number | null,
): SetList {
  return sets.map((set, i) => {
    if (i !== index) return set;
    if (field === 'rpe' || field === 'duration_seconds' || field.endsWith('_left') || field.endsWith('_right')) {
      return { ...set, [field]: value == null || Number.isNaN(Number(value)) ? null : Number(value) };
    }
    if (field === 'reps' && setEditFields(set.measure).repsMin != null && (value == null || Number.isNaN(Number(value)))) {
      return { ...set, reps: null };
    }
    return { ...set, [field]: Number(value) || 0 };
  });
}
