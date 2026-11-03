import { apiFetch } from '@/lib/api';
import { suggestedProgressionTargets } from '@/lib/suggested-progression';
import type { SuggestedProgressionResponse, WorkoutPlanExercise } from '@/lib/workout-plans-types';

/**
 * Campos da linha da ficha que a sugestão de progressão do servidor
 * (`GET /exercises/suggested-progression`) sobrescreve ao vincular um exercício
 * do catálogo. Sem faixa do motor o campo fica vazio, nunca inventado.
 */
export async function fetchSuggestedProgressionPatch(mappingId: number): Promise<Partial<WorkoutPlanExercise>> {
  const suggestion = await apiFetch<SuggestedProgressionResponse>(`/exercises/suggested-progression?mapping_id=${mappingId}`);
  const targets = suggestedProgressionTargets(suggestion);
  return {
    reps_target: targets.reps_target,
    load_target: targets.load_target,
    rpe_target: targets.rpe_target,
    progression_note: targets.progression_note,
    progression: suggestion.progression ?? null,
  };
}
