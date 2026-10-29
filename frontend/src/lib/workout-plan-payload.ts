// Mapper único ficha/rascunho -> payload de save (POST/PUT /workout-plans).
// Espelha WorkoutPlanExerciseIn/WorkoutPlanIn de `api/app/models.py`. Antes
// havia três mapeamentos manuais (GenerateDraftModal, PlanDetailClient,
// ModeSelectScreen) que descartavam os campos avançados do modo
// 'equipment_series' — ao adicionar um campo no backend, adicione-o AQUI.
import type {
  WorkoutPlanDraft,
  WorkoutPlanExercise,
  WorkoutPlanExerciseInput,
  WorkoutPlanInput,
} from './workout-plans-types';

export function toWorkoutPlanExerciseInput(ex: WorkoutPlanExercise, sequence: number): WorkoutPlanExerciseInput {
  return {
    sequence,
    workout_plan_exercise_id: ex.workout_plan_exercise_id ?? null,
    exercise_name: ex.exercise_name,
    amazfit_name: ex.amazfit_name ?? null,
    mapping_id: ex.mapping_id ?? null,
    preferred_equipment_id: ex.preferred_equipment_id ?? null,
    muscle_primary: ex.muscle_primary ?? null,
    muscle_secondary: ex.muscle_secondary ?? null,
    equipment_name: ex.equipment_name ?? null,
    sets_count: ex.sets_count ?? null,
    reps_target: ex.reps_target ?? null,
    load_target: ex.load_target ?? null,
    rpe_target: ex.rpe_target ?? null,
    rest_seconds: ex.rest_seconds ?? null,
    warmup: !!ex.warmup,
    substitution_options: ex.substitution_options ?? null,
    notes: ex.notes ?? null,
    advanced_technique: ex.advanced_technique ?? null,
    advanced_technique_applications: ex.advanced_technique_applications ?? null,
    advanced_technique_duration_seconds: ex.advanced_technique_duration_seconds ?? null,
    warmup_sets_count: ex.warmup_sets_count ?? null,
    warmup_reps: ex.warmup_reps ?? null,
    warmup_rest_seconds: ex.warmup_rest_seconds ?? null,
    feeler_set_sets_count: ex.feeler_set_sets_count ?? null,
    feeler_set_reps: ex.feeler_set_reps ?? null,
    feeler_set_rest_seconds: ex.feeler_set_rest_seconds ?? null,
    peak_contraction_seconds: ex.peak_contraction_seconds ?? null,
    // Medida da série (Lote T2): repassada como veio. `undefined` some do JSON
    // e o save preserva o valor gravado (`workout_plan_exercise_merge`); assim
    // um objeto sem o campo nunca apaga a medida por tempo da linha.
    measure: ex.measure,
    duration_seconds_target: ex.duration_seconds_target,
    time_window_seconds: ex.time_window_seconds,
    // M97 F3: objetivo da linha, como a medida: repassado como veio. O servidor
    // carimba o do Modelo no congelamento (`plan_goal_stamp`), mas o cliente
    // não perde o campo do rascunho nem da ficha gravada.
    training_goal: ex.training_goal,
  };
}

/**
 * `sequenceFromIndex`: true (padrão) renumera 1..N pela ordem da lista (a
 * ordem visível na tela é a fonte da verdade); false usa `ex.sequence`
 * quando existir (rascunho salvo sem edição, ModeSelectScreen).
 */
export function toWorkoutPlanExercisesInput(
  exercises: WorkoutPlanExercise[] | null | undefined,
  { sequenceFromIndex = true }: { sequenceFromIndex?: boolean } = {},
): WorkoutPlanExerciseInput[] {
  return (exercises || []).map((ex, index) =>
    toWorkoutPlanExerciseInput(ex, sequenceFromIndex ? index + 1 : ex.sequence || index + 1),
  );
}

/**
 * raw_payload persistido a partir de um rascunho gerado: resumo com tudo que
 * a tela de detalhe reexibe depois de salvo (resumo da geração + exclusões),
 * já que o backend não persiste essas listas em colunas próprias.
 *
 * `template_slot_targets` (M76 E4): alvo por grupo do slot, em séries
 * equivalentes, lido por `/analytics/muscle-activation-by-activity` para a
 * coluna "Meta" de `/registro-exercicios/[activityId]`. Desde 2026-09-13 não
 * era mais persistido (a tabela caía na meta genérica).
 */
export function buildDraftRawPayload(draft: WorkoutPlanDraft): Record<string, unknown> {
  return {
    template_slot_targets: draft.template_slot_targets ?? null,
    generation_summary: draft.generation_summary ?? null,
    excluded_due_to_recovery: draft.excluded_due_to_recovery ?? [],
    excluded_due_to_no_equipment: draft.excluded_due_to_no_equipment ?? [],
    reduced_dose_groups: draft.reduced_dose_groups ?? [],
    insufficient_target_volume_groups: draft.insufficient_target_volume_groups ?? [],
    adjusted_due_to_catalog_mismatch: draft.adjusted_due_to_catalog_mismatch ?? [],
    generation_context: draft.raw_payload ?? null,
    // M98 C0: a ficha não tem coluna de cardio; sem isto o cardio do slot do
    // Modelo se perdia no save (o `WorkoutPlanIn` descarta campo desconhecido).
    cardio_minutes: draft.cardio_minutes ?? null,
    cardio_prescriptions: draft.cardio_prescriptions ?? [],
  };
}

type PlanFields = Omit<WorkoutPlanInput, 'exercises'>;

export function toWorkoutPlanInput(
  fields: PlanFields,
  exercises: WorkoutPlanExercise[] | null | undefined,
  options?: { sequenceFromIndex?: boolean },
): WorkoutPlanInput {
  return { ...fields, exercises: toWorkoutPlanExercisesInput(exercises, options) };
}
