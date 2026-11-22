// RPE por série na linha da ficha (#184). Só decide o que mostrar e o que
// enviar; escala, passo e a composição do texto são do servidor (dono
// `training_history.compose_rpe_target`) e do espelho `RPE_TARGET_SCALE`.
import { fitRpeTargets, storedRpeTextOnly } from '@/lib/workout-plan-template-form';
import type { WorkoutPlanExercise } from '@/lib/workout-plans-types';

type RpeRow = Pick<WorkoutPlanExercise, 'rpe_target' | 'rpe_targets_by_set' | 'rpe_edited' | 'sets_count'>;

/** Linha -> props de `RpePerSetField`: lista por série e texto gravado (só sem lista e sem edição). */
export function rpePerSetView(ex: RpeRow): { targets: number[]; storedText: string | null } {
  const targets = ex.rpe_targets_by_set ?? [];
  return { targets, storedText: storedRpeTextOnly({ rpe_target: ex.rpe_target ?? null, rpe_targets_by_set: targets, rpe_edited: ex.rpe_edited }) };
}

/** Commit do campo por série -> patch da linha. O texto antigo sai: a lista passa a ser a fonte. */
export function rpePerSetPatch(targets: number[]): Pick<WorkoutPlanExercise, 'rpe_targets_by_set' | 'rpe_target' | 'rpe_edited'> {
  return { rpe_targets_by_set: targets, rpe_target: null, rpe_edited: true };
}

/**
 * Campos de RPE do payload. Editado por série: só a lista (ajustada às séries da
 * linha), sem `rpe_target` conflitante. Não editado: o texto como sempre foi.
 */
export function rpePayloadFields(ex: RpeRow): { rpe_target?: string | null; rpe_targets_by_set?: number[] } {
  if (ex.rpe_edited === true) {
    return { rpe_targets_by_set: fitRpeTargets(ex.rpe_targets_by_set ?? [], ex.sets_count ?? null) };
  }
  return { rpe_target: ex.rpe_target ?? null };
}
