// Resumo do slot recolhido no editor de Modelo (`/modelos-treino/[id]`):
// séries de trabalho e exercícios por GRUPO PRINCIPAL, um por linha
// ("Costas  14 séries/4 exercícios").
//
// Grupo principal: o `muscle_group_id` primário do item sobe a cadeia
// `muscle_groups.methodology_parent_group_id` até a raiz
// (`resolvePrincipalGroupId`) — a mesma regra de bloco do motor da
// metodologia (`_resolve_block` no backend). Subgrupos ("Dorsal alto") não
// aparecem: somam no principal ("Costas").
//
// Modo por equipamento: `slotGroupSets` só AGRUPA os itens (grupo principal,
// subgrupos que somaram e nº de exercícios). As SÉRIES não são somadas aqui
// (M76 E5): vêm do preview da metodologia do backend (`direct_sets` da sessão
// do bloco — contribuição PRIMARY, com o equivalente da técnica), o mesmo
// número "9/12" exibido ao lado; ver `slotGroupSetsWithStatus`.
//
// "Exercícios" = itens não-cardio com grupo que caem no grupo principal,
// MESMO com 0 séries (ou séries em branco): o item existe no dia e o número
// bate com o que se vê ao expandir.
//
// Modo por orçamento (`percent_budget`): `slotGroupBudgets` — os itens de grupo
// não têm séries (o volume é a meta semanal do tipo de treino × faixa %), então
// a linha mostra a faixa e as séries projetadas pelo backend
// (`item_budgets` do preview de ativação), com a mesma agregação por grupo
// principal: "Costas  20–30% · ≈3–4 séries/2 exercícios".

import type { ItemState } from './workout-plan-template-form';
import type { PreviewActivationItemBudget } from './workout-plan-templates-types';

/** Uma linha do resumo recolhido: grupo PRINCIPAL + rótulo já formatado. */
export type SlotGroupSummaryLine = {
  /** Id do grupo principal (raiz da cadeia de agregação). */
  muscleGroupId: number;
  name: string;
  label: string;
  /**
   * Grupos dos itens que somaram nesta linha (o principal e/ou subgrupos),
   * sem repetição, na ordem em que aparecem no slot. Usado para o status.
   */
  memberGroupIds: number[];
  /** Itens (exercícios) do slot que caem neste grupo principal. */
  exercises: number;
};

/** Linha do modo por equipamento antes das séries do backend (só o agrupamento). */
export type SlotGroupSetsRow = SlotGroupSummaryLine;

/** `muscle_group_id -> methodology_parent_group_id` (null/ausente = raiz). */
export type MuscleGroupParentMap = ReadonlyMap<number, number | null | undefined>;

/** Teto defensivo da cadeia (o backend já recusa ciclo; a árvore real tem 1–2 níveis). */
const MAX_PARENT_DEPTH = 32;

/**
 * Grupo principal de um grupo: sobe `methodology_parent_group_id` até a raiz.
 * Mesma regra do `_resolve_block` do motor: pai fora do mapa (lista ainda
 * carregando, grupo apagado) para no grupo atual; ciclo de cadastro para no
 * último visitado antes de repetir. Sem mapa, cada grupo é o próprio principal.
 */
export function resolvePrincipalGroupId(groupId: number, parentById?: MuscleGroupParentMap): number {
  if (!parentById) return groupId;
  const seen = new Set<number>([groupId]);
  let current = groupId;
  for (let depth = 0; depth < MAX_PARENT_DEPTH; depth += 1) {
    const parent = parentById.get(current);
    if (parent == null || parent === current || !parentById.has(parent) || seen.has(parent)) return current;
    seen.add(parent);
    current = parent;
  }
  return current;
}

type SlotLike = { items: ReadonlyArray<Pick<ItemState, 'item_kind' | 'muscle_group_id'>> };

/** "1 série" / "N séries". */
export function formatSeriesCount(sets: number): string {
  return sets === 1 ? '1 série' : `${sets} séries`;
}

/** "1 exercício" / "N exercícios". */
export function formatExerciseCount(count: number): string {
  return count === 1 ? '1 exercício' : `${count} exercícios`;
}

function groupName(groupId: number, nameById: ReadonlyMap<number, string>): string {
  return nameById.get(groupId) || `Grupo #${groupId}`;
}

type Accumulator = { memberGroupIds: number[]; exercises: number };

/** Conta o item no grupo principal (cria a entrada na 1ª vez) e devolve a entrada. */
function accumulate<T extends Accumulator>(
  totals: Map<number, T>,
  principalId: number,
  memberId: number,
  create: () => T,
): T {
  let entry = totals.get(principalId);
  if (!entry) {
    entry = create();
    totals.set(principalId, entry);
  }
  if (!entry.memberGroupIds.includes(memberId)) entry.memberGroupIds.push(memberId);
  entry.exercises += 1;
  return entry;
}

/**
 * Itens do slot por grupo PRINCIPAL (nome, subgrupos que somaram e nº de
 * exercícios), em ordem alfabética (pt-BR) — a ordem final, por séries, é a de
 * `slotGroupSetsWithStatus`, que traz as séries do backend. Itens de cardio ou
 * sem grupo são ignorados; item sem séries conta como exercício. Grupo sem nome
 * no mapa (lista de grupos ainda carregando) aparece como "Grupo #id".
 */
export function slotGroupSets(
  slot: SlotLike,
  nameById: ReadonlyMap<number, string>,
  parentById?: MuscleGroupParentMap,
): SlotGroupSetsRow[] {
  const totals = new Map<number, Accumulator>();
  for (const item of slot.items) {
    if (item.item_kind === 'cardio') continue;
    const groupId = item.muscle_group_id;
    if (groupId == null) continue;
    accumulate(totals, resolvePrincipalGroupId(groupId, parentById), groupId, () => ({
      memberGroupIds: [],
      exercises: 0,
    }));
  }
  const rows: SlotGroupSetsRow[] = [];
  for (const [muscleGroupId, { exercises, memberGroupIds }] of totals) {
    rows.push({
      muscleGroupId,
      name: groupName(muscleGroupId, nameById),
      exercises,
      memberGroupIds,
      label: formatExerciseCount(exercises),
    });
  }
  rows.sort((a, b) => a.name.localeCompare(b.name, 'pt-BR'));
  return rows;
}

export type SlotGroupBudgetRow = SlotGroupSummaryLine & {
  /** Soma das faixas % dos itens do grupo no slot. `null`: nenhuma faixa conhecida. */
  pctMin: number | null;
  pctMax: number | null;
  /** Séries projetadas (soma). `null` sem projeção (preview carregando ou sem meta). */
  setsMin: number | null;
  setsMax: number | null;
  /** `false` quando o tipo de treino não tem meta para o grupo; `null` sem projeção. */
  hasTarget: boolean | null;
};

type BudgetSlotLike = {
  items: ReadonlyArray<Pick<ItemState, 'item_kind' | 'muscle_group_id' | 'priority_min_pct' | 'priority_max_pct'>>;
};

type BudgetLike = Pick<
  PreviewActivationItemBudget,
  'muscle_group_id' | 'pct_min' | 'pct_max' | 'sets_min' | 'sets_max' | 'has_target'
>;

/** "20–30%" (inteiros, menor primeiro); "25%" quando os dois lados coincidem. */
function formatPctRange(min: number, max: number): string {
  const low = Math.round(Math.min(min, max));
  const high = Math.round(Math.max(min, max));
  return low === high ? `${low}%` : `${low}–${high}%`;
}

/** "≈3–4 séries" / "≈3 séries" / "≈1 série" (inteiros). */
function formatProjectedSets(min: number, max: number): string {
  const low = Math.round(Math.min(min, max));
  const high = Math.round(Math.max(min, max));
  return low === high ? `≈${formatSeriesCount(low)}` : `≈${low}–${high} séries`;
}

type BudgetValues = Pick<SlotGroupBudgetRow, 'pctMin' | 'pctMax' | 'setsMin' | 'setsMax' | 'hasTarget'>;

function budgetLabel(values: BudgetValues): string {
  const pct =
    values.pctMin != null && values.pctMax != null ? formatPctRange(values.pctMin, values.pctMax) : 'faixa automática';
  if (values.hasTarget === false) return `${pct} · sem meta`;
  if (values.setsMin != null && values.setsMax != null) {
    return `${pct} · ${formatProjectedSets(values.setsMin, values.setsMax)}`;
  }
  return pct;
}

function addOrKeep(total: number | null, value: number | null): number | null {
  return value == null ? total : (total ?? 0) + value;
}

/**
 * Modo por orçamento: TODOS os grupos PRINCIPAIS dos itens `muscle_group` do
 * slot, com ou sem séries, um por linha. Itens do mesmo grupo principal (o
 * próprio e os subgrupos) somam faixas e séries: a faixa % de cada item é a
 * fatia DO SLOT que ele ocupa (os itens do slot dividem 100%, ver
 * `project_muscle_item_budgets`) e as séries projetadas já estão em séries —
 * as duas grandezas são aditivas. "Exercícios" = itens `muscle_group` que
 * caem no principal.
 *
 * `itemBudgets` são os `item_budgets` do preview DESTE slot (já filtrados pelo
 * `slot_sequence`): trazem a faixa efetiva (inclusive a divisão automática dos
 * itens sem %) e as séries projetadas. Sem eles (preview carregando, Modelo
 * sem tipo de treino), a linha mostra só a faixa configurada nos itens.
 * `hasTarget`: basta um item da linha ter meta; as séries somam só dos que têm.
 *
 * Ordem: maior faixa primeiro (empate: nome, pt-BR). Grupo sem nome no mapa
 * aparece como "Grupo #id".
 */
export function slotGroupBudgets(
  slot: BudgetSlotLike,
  nameById: ReadonlyMap<number, string>,
  itemBudgets?: ReadonlyArray<BudgetLike>,
  parentById?: MuscleGroupParentMap,
): SlotGroupBudgetRow[] {
  const groups = new Map<number, Accumulator>();
  for (const item of slot.items) {
    if (item.item_kind !== 'muscle_group' || item.muscle_group_id == null) continue;
    const principalId = resolvePrincipalGroupId(item.muscle_group_id, parentById);
    accumulate(groups, principalId, item.muscle_group_id, () => ({ memberGroupIds: [], exercises: 0 }));
  }

  const rows: SlotGroupBudgetRow[] = [];
  for (const [muscleGroupId, { memberGroupIds, exercises }] of groups) {
    const values: BudgetValues = { pctMin: null, pctMax: null, setsMin: null, setsMax: null, hasTarget: null };
    const budgets = itemBudgets?.filter((budget) => memberGroupIds.includes(budget.muscle_group_id)) ?? [];
    if (budgets.length > 0) {
      for (const budget of budgets) {
        values.pctMin = addOrKeep(values.pctMin, budget.pct_min);
        values.pctMax = addOrKeep(values.pctMax, budget.pct_max);
        values.setsMin = addOrKeep(values.setsMin, budget.sets_min);
        values.setsMax = addOrKeep(values.setsMax, budget.sets_max);
        values.hasTarget = (values.hasTarget ?? false) || budget.has_target;
      }
    } else {
      for (const item of slot.items) {
        if (item.item_kind !== 'muscle_group' || item.muscle_group_id == null) continue;
        if (!memberGroupIds.includes(item.muscle_group_id)) continue;
        values.pctMin = addOrKeep(values.pctMin, item.priority_min_pct);
        values.pctMax = addOrKeep(values.pctMax, item.priority_max_pct);
      }
      // Só um lado preenchido não é faixa: fica "automática".
      if (values.pctMin == null || values.pctMax == null) {
        values.pctMin = null;
        values.pctMax = null;
      }
    }

    rows.push({
      muscleGroupId,
      name: groupName(muscleGroupId, nameById),
      memberGroupIds,
      exercises,
      ...values,
      label: `${budgetLabel(values)}/${formatExerciseCount(exercises)}`,
    });
  }

  rows.sort((a, b) => (b.pctMax ?? -1) - (a.pctMax ?? -1) || a.name.localeCompare(b.name, 'pt-BR'));
  return rows;
}
