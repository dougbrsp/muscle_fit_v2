// Troca de exercício numa linha JÁ SALVA da ficha não finalizada.
//
// A regra de "exercícios elegíveis" é do servidor
// (`api/app/workout_plan_edit_rules.py::eligible_swap_exercise_ids`, a mesma
// da validação 422 `muscle_group_locked`) e chega pronta em
// `GET /workout-plans/{id}/exercise-swap-options`. A tela só filtra o catálogo
// do autocomplete pelos ids devolvidos — nunca compara texto de grupo.

export type ExerciseSwapOptionsRow = {
  workout_plan_exercise_id: number;
  primary_group_id: number | null;
  // null = troca sem restrição (o servidor não compara grupo nesta linha).
  exercise_ids: number[] | null;
};

export type ExerciseSwapOptionsResponse = {
  plan_id: number;
  editable: boolean;
  rows: ExerciseSwapOptionsRow[];
};

export type SwapEligibility =
  | { kind: 'free' }
  | { kind: 'restricted'; ids: ReadonlySet<number> }
  | { kind: 'loading' };

export type SwapEligibilityIndex = ReadonlyMap<number, ExerciseSwapOptionsRow>;

export function indexSwapOptions(response: ExerciseSwapOptionsResponse | undefined): SwapEligibilityIndex | undefined {
  if (!response) return undefined;
  return new Map(response.rows.map((row) => [row.workout_plan_exercise_id, row]));
}

/**
 * Elegibilidade de uma linha da tabela.
 * - Linha nova (sem id) ou tabela sem linhas salvas (`index` ausente, ex.:
 *   rascunho gerado): livre.
 * - Linha salva com a resposta do servidor ainda chegando: nada é oferecido.
 * - Linha salva ausente da resposta (ficha salva depois da leitura, ou a
 *   leitura falhou): nada é oferecido — o servidor é quem decide.
 */
export function swapEligibilityFor(
  workoutPlanExerciseId: number | null | undefined,
  index: SwapEligibilityIndex | 'loading' | undefined,
): SwapEligibility {
  if (workoutPlanExerciseId == null || index === undefined) return { kind: 'free' };
  if (index === 'loading') return { kind: 'loading' };
  const row = index.get(workoutPlanExerciseId);
  if (!row) return { kind: 'restricted', ids: new Set() };
  if (row.exercise_ids == null) return { kind: 'free' };
  return { kind: 'restricted', ids: new Set(row.exercise_ids) };
}

export function filterBySwapEligibility<T extends { mappingId: number | null }>(
  items: readonly T[],
  eligibility: SwapEligibility,
): T[] {
  if (eligibility.kind === 'free') return items as T[];
  if (eligibility.kind === 'loading') return [];
  return items.filter((item) => item.mappingId != null && eligibility.ids.has(item.mappingId));
}
