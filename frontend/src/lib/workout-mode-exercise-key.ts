import { isSyntheticPlanExerciseId } from './workout-mode-types';

// Slot da `client_key` de criação do exercício (o servidor deduplica por ela).
// Item real: `sessão#idDoItem` (sessões em andamento já guardam essas chaves).
// Continuação sintética (id negativo): usa o id DELA, senão repetiria a chave do
// item original e o servidor devolveria o exercício original (`duplicate`).
export function exerciseKeySlot(params: {
  sessionId: number | string;
  /** `workout_plan_exercise_id` do item atual (real ou sintético). */
  planItemId: number | null | undefined;
  /** Item da ficha enviado ao servidor (o ORIGINAL, no caso de continuação). */
  originalPlanItemId: number | null | undefined;
  exerciseName: string;
}): string {
  const { sessionId, planItemId, originalPlanItemId, exerciseName } = params;
  const id = planItemId != null && isSyntheticPlanExerciseId(planItemId) ? planItemId : (originalPlanItemId ?? exerciseName);
  return `${sessionId}#${id}`;
}
