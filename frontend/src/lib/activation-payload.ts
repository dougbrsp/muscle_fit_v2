// Payload ÚNICO de ativação planejada (M76 E3): os dados da linha, com os
// nomes do item do Modelo. O backend (`technique_volume.planned_item_sets`,
// Fase 5) soma o equivalente da técnica avançada; aqui não há conta nenhuma.
// Usado pela prévia de ativação da ficha (`MuscleActivationPreview`) e pelo
// detalhe por linha (`ExercisesTable`, `/analytics/muscle-activation-breakdown`).

import type { MuscleActivationPreviewItem, WorkoutPlanExercise } from './workout-plans-types';

export function toActivationPayloadItem(
  exercise: Pick<
    WorkoutPlanExercise,
    'mapping_id' | 'sets_count' | 'advanced_technique' | 'advanced_technique_applications' | 'is_laboral'
  >,
): MuscleActivationPreviewItem {
  return {
    mapping_id: exercise.mapping_id ?? null,
    sets_count: exercise.sets_count ?? null,
    advanced_technique: exercise.advanced_technique ?? null,
    advanced_technique_applications: exercise.advanced_technique_applications ?? null,
    ...(exercise.is_laboral ? { is_laboral: true } : {}),
  };
}
