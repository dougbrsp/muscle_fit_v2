// Troca de exercício numa linha da ficha não finalizada ou do rascunho gerado.
//
// A regra de "exercícios elegíveis" é do servidor
// (`api/app/workout_plan_edit_rules.py::eligible_swap_exercise_ids`, a mesma
// da validação 422 `muscle_group_locked`) e chega pronta em
// `GET /workout-plans/{id}/exercise-swap-options` (ficha salva) ou
// `POST /workout-plans/exercise-swap-options` (rascunho). A tela só filtra o catálogo
// do autocomplete pelos ids devolvidos — nunca compara texto de grupo.

import type { DraftEquipmentOptionsRequest, PlanExerciseEquipmentOption } from './workout-plans-types';

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

export type SwapEligibilityIndex = ReadonlyMap<number, { exercise_ids: number[] | null }>;

export function indexSwapOptions(response: ExerciseSwapOptionsResponse | undefined): SwapEligibilityIndex | undefined {
  if (!response) return undefined;
  return new Map(response.rows.map((row) => [row.workout_plan_exercise_id, row]));
}

// ---- Rascunho recém-gerado (sem id de servidor nas linhas) ----
// `POST /workout-plans/exercise-swap-options` recebe os `mapping_id` das linhas
// geradas e devolve, por `mapping_id`, os ids elegíveis (mesma regra do servidor).

export type DraftSwapOptionsItem = {
  mapping_id: number;
  primary_group_id: number | null;
  // null = troca sem restrição.
  exercise_ids: number[] | null;
};

export type DraftSwapOptionsResponse = { items: DraftSwapOptionsItem[] };

// Estado repassado aos editores: ausente = sem rascunho a consultar.
export type DraftSwapOptions = SwapEligibilityIndex | 'loading' | 'error';

/**
 * Chave local da linha gerada: o `mapping_id` com que o servidor a entregou.
 * Vive na própria linha (sobrevive a reordenar, remover e ao spread de cada
 * edição) e NÃO entra no payload de save, que copia campo a campo
 * (`toWorkoutPlanExerciseInput`). Linha adicionada à mão não a tem.
 */
export type DraftRowKey = { generated_mapping_id?: number | null };

export function stampGeneratedRows<T extends { mapping_id?: number | null }>(rows: readonly T[]): (T & DraftRowKey)[] {
  return rows.map((row) => ({ ...row, generated_mapping_id: row.mapping_id ?? null }));
}

export function generatedMappingIds(rows: readonly { mapping_id?: number | null }[] | undefined): number[] {
  const ids = new Set<number>();
  for (const row of rows ?? []) if (row.mapping_id != null && row.mapping_id > 0) ids.add(row.mapping_id);
  return [...ids].sort((a, b) => a - b);
}

export function draftSwapOptionsState(
  query: { data?: DraftSwapOptionsResponse; isError: boolean },
  hasIds: boolean,
): DraftSwapOptions | undefined {
  if (!hasIds) return undefined;
  if (query.data) return new Map(query.data.items.map((item) => [item.mapping_id, item]));
  return query.isError ? 'error' : 'loading';
}

/**
 * Elegibilidade de uma linha, salva ou do rascunho.
 * - Linha salva: `swapEligibilityFor` por `workout_plan_exercise_id`.
 * - Linha gerada: restrita pelo `mapping_id` com que foi GERADA (não o atual).
 * - Linha nova à mão, ou gerada sem `mapping_id`: livre.
 */
export function rowSwapEligibility(
  row: { workout_plan_exercise_id?: number | null; generated_mapping_id?: number | null },
  swapOptions: SwapEligibilityIndex | 'loading' | undefined,
  draftSwapOptions: DraftSwapOptions | undefined,
): SwapEligibility {
  if (row.workout_plan_exercise_id != null) return swapEligibilityFor(row.workout_plan_exercise_id, swapOptions);
  if (row.generated_mapping_id == null) return { kind: 'free' };
  if (draftSwapOptions === 'error') return { kind: 'restricted', ids: new Set() };
  return swapEligibilityFor(row.generated_mapping_id, draftSwapOptions);
}

/**
 * Ao trocar para OUTRO exercício, o equipamento (e a sugestão de carga que o
 * acompanha) do anterior não vale mais: a linha volta ao estado "sem
 * equipamento" em vez de o servidor descartar o vínculo em silêncio ao salvar.
 */
export function swapExerciseResetPatch(
  previousMappingId: number | null | undefined,
  nextMappingId: number | null,
): { preferred_equipment_id?: null; equipment_name?: null; suggested_load_kg?: null } {
  if (previousMappingId === nextMappingId) return {};
  return { preferred_equipment_id: null, equipment_name: null, suggested_load_kg: null };
}

// ---- Equipamento no rascunho (lista e elegibilidade vêm do servidor) ----

type DraftEquipmentRow = {
  mapping_id?: number | null;
  preferred_equipment_id?: number | null;
  advanced_technique?: string | null;
};

/** Corpo do `POST /workout-plans/equipment-options`; null = linha sem exercício do catálogo. */
export function draftEquipmentOptionsBody(
  row: DraftEquipmentRow,
  gymId: number,
): DraftEquipmentOptionsRequest | null {
  if (row.mapping_id == null) return null;
  return {
    gym_id: gymId,
    mapping_id: row.mapping_id,
    current_gym_equipment_id: row.preferred_equipment_id ?? null,
    advanced_technique: row.advanced_technique ?? null,
  };
}

/** A chave inclui tudo que o corpo leva: trocar exercício/equipamento/técnica refaz a consulta. */
export function draftEquipmentOptionsKey(body: DraftEquipmentOptionsRequest | null) {
  return ['draft-equipment-options', body?.gym_id, body?.mapping_id, body?.current_gym_equipment_id, body?.advanced_technique];
}

/** Escolha do usuário: só a linha local muda; a carga é resolvida pelo servidor ao salvar. */
export function applyDraftEquipmentChoice<T extends { preferred_equipment_id?: number | null; equipment_name?: string | null }>(
  row: T,
  item: Pick<PlanExerciseEquipmentOption, 'gym_equipment_id' | 'equipment_name'>,
): T {
  return { ...row, preferred_equipment_id: item.gym_equipment_id, equipment_name: item.equipment_name };
}

/**
 * Último exercício vinculado à linha antes de a digitação zerar `mapping_id`.
 * Campo só do cliente (como `generated_mapping_id`): fica fora do payload de save.
 */
export type LinkedRowKey = { last_linked_mapping_id?: number | null };

type LinkRow = { mapping_id?: number | null } & LinkedRowKey;

/** Digitação: se o texto deixa de bater com o vínculo, guarda o vínculo anterior (o primeiro da sequência de teclas). */
export function typedExercisePatch(
  row: LinkRow,
  value: string,
  matchesLinked: boolean,
): { exercise_name: string; mapping_id: number | null; last_linked_mapping_id: number | null } {
  const lastLinked = row.mapping_id ?? row.last_linked_mapping_id ?? null;
  return {
    exercise_name: value,
    mapping_id: matchesLinked ? (row.mapping_id ?? null) : null,
    last_linked_mapping_id: lastLinked,
  };
}

/** Escolha na lista: compara com o último vínculo (antes da digitação) e limpa equipamento/carga só se for outro exercício. */
export function pickedExercisePatch(row: LinkRow, name: string, mappingId: number | null) {
  const previous = row.mapping_id ?? row.last_linked_mapping_id ?? null;
  return {
    exercise_name: name,
    mapping_id: mappingId,
    last_linked_mapping_id: mappingId,
    ...swapExerciseResetPatch(previous, mappingId),
  };
}

/**
 * Elegibilidade de uma linha da tabela.
 * - Linha nova (sem id) ou tabela sem linhas salvas (`index` ausente): livre.
 *   O rascunho gerado usa `rowSwapEligibility`.
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
