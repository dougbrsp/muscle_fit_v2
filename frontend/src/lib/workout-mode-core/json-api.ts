// Adaptadores JSON da fila do Modo Treino: as mesmas funções de
// `workout-mode-plan-flow.ts`, com `Set` e callbacks trocados por listas de
// ids. Servem ao cliente que guarda o estado em JSON (o relógio) e ao golden
// do núcleo, que só carrega argumentos JSON. Nenhuma regra nova: só montam o
// `Set`/callback e delegam. A chave de item é a mesma da tela
// (`planExercise.workout_plan_exercise_id ?? -1`, `PlanExecutionScreen.tsx`).

import {
  buildExecutionQueue,
  findPlanItemForSubstitutedExercise,
  findPlanItemsCoveredByAnotherSlot,
  reorderPendingBySkip,
} from '../workout-mode-plan-flow';
import type { ExerciseLogRow, WorkoutPlanExercise } from '../workout-mode-types';

/** `buildExecutionQueue` com `cancelledIds` em lista. */
export function buildExecutionQueueFromIds<T extends WorkoutPlanExercise>(params: {
  planExercises: ReadonlyArray<T>;
  syntheticExercises: ReadonlyArray<T>;
  cancelledIds: ReadonlyArray<number>;
}): T[] {
  return buildExecutionQueue({ ...params, cancelledIds: new Set(params.cancelledIds) });
}

/** `reorderPendingBySkip` chaveado pelo item da ficha, com `skippedIds` em lista. */
export function reorderPendingBySkipByPlanItem<T extends { done: boolean; planExercise: WorkoutPlanExercise }>(
  items: ReadonlyArray<T>,
  skippedIds: ReadonlyArray<number>,
  priorityId: number | null,
): T[] {
  return reorderPendingBySkip(
    items,
    (item) => item.planExercise.workout_plan_exercise_id ?? -1,
    new Set(skippedIds),
    priorityId,
  );
}

/** `findPlanItemForSubstitutedExercise` com os itens pendentes em lista. */
export function findPlanItemForSubstitutedExerciseFromIds(params: {
  planExercises: ReadonlyArray<WorkoutPlanExercise>;
  chosenMappingId: number | null | undefined;
  currentWorkoutPlanExerciseId: number | null;
  pendingIds: ReadonlyArray<number>;
}): number | null {
  const { pendingIds, ...rest } = params;
  return findPlanItemForSubstitutedExercise({ ...rest, isPending: (id) => pendingIds.includes(id) });
}

/**
 * `findPlanItemsCoveredByAnotherSlot` devolvendo lista (na ordem da ficha) no
 * lugar de `Set`. Basta o subconjunto dos campos que a função lê de cada
 * exercício gravado (`workout_plan_exercise_id`, `mapping_id`, `planned_mapping_id`).
 */
export function findPlanItemsCoveredByAnotherSlotIds(params: {
  planExercises: ReadonlyArray<Pick<WorkoutPlanExercise, 'workout_plan_exercise_id' | 'mapping_id'>>;
  loggedExercises: ReadonlyArray<Pick<ExerciseLogRow, 'workout_plan_exercise_id' | 'mapping_id' | 'planned_mapping_id'>>;
}): number[] {
  return Array.from(findPlanItemsCoveredByAnotherSlot(params));
}
