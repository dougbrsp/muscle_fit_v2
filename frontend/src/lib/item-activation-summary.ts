import { isActivationContributingItem } from '@/lib/activation-preview-payload';
import type {
  ActivationRole,
  PreviewActivationGroupTotal,
  PreviewActivationItem,
  PreviewActivationItemContribution,
  PreviewActivationResponse,
  WorkoutPlanTemplateSlotItemPayload,
  WorkoutPlanTemplateSlotPayload,
} from '@/lib/workout-plan-templates-types';

/**
 * M11 — linha de ativação abaixo de cada item do builder de modelos.
 *
 * Tudo aqui é FORMATAÇÃO/AGRUPAMENTO de números que já vêm do servidor
 * (`POST /workout-plan-templates/preview-activation` → `items[]` e
 * `group_totals[]`). Nenhum estímulo é calculado no cliente (PR-13/G7).
 *
 * Unidade: séries equivalentes (séries × stimulus_weight) na janela do
 * preview — a mesma da tabela "Preview de ativação semanal" (total
 * primário + secundário + estabilizador). Numerador = contribuição da linha;
 * denominador = total do grupo em todos os slots da janela.
 */

export type ItemActivationEntry = {
  /** Id do grupo no catálogo (M43 casa com o bloco da metodologia); null em resposta antiga. */
  muscleGroupId: number | null;
  muscleGroup: string;
  value: number;
  total: number;
  /** "Bíceps: 2,5/5,0" */
  text: string;
};

export type ItemActivationSummary = Record<ActivationRole, ItemActivationEntry[]>;

export const ACTIVATION_ROLE_ORDER: ActivationRole[] = ['primary', 'secondary', 'stabilizer'];

export function formatActivationNumber(value: number): string {
  return value.toLocaleString('pt-BR', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
}

export function formatActivationEntryText(muscleGroup: string, value: number, total: number): string {
  return `${muscleGroup}: ${formatActivationNumber(value)}/${formatActivationNumber(total)}`;
}

/**
 * Agrupa as contribuições de UM item por papel. Grupos sem total conhecido
 * (ou zerado) ficam de fora — sem denominador não há o que mostrar, e nada é
 * inventado. Devolve `null` quando não sobra nenhuma entrada.
 */
export function summarizeItemActivation(
  contributions: PreviewActivationItemContribution[],
  groupTotals: PreviewActivationGroupTotal[],
): ItemActivationSummary | null {
  const totalByGroup = new Map(groupTotals.map((g) => [g.muscle_group, g.total_equivalent]));
  const summary: ItemActivationSummary = { primary: [], secondary: [], stabilizer: [] };

  for (const contribution of contributions) {
    const total = totalByGroup.get(contribution.muscle_group);
    if (!(contribution.value > 0) || total == null || !(total > 0)) continue;
    // O servidor arredonda numerador e denominador a 2 casas separadamente;
    // o `min` só impede que esse arredondamento mostre a linha acima do total.
    const value = Math.min(contribution.value, total);
    summary[contribution.role]?.push({
      muscleGroupId: contribution.muscle_group_id ?? null,
      muscleGroup: contribution.muscle_group,
      value,
      total,
      text: formatActivationEntryText(contribution.muscle_group, value, total),
    });
  }

  for (const role of ACTIVATION_ROLE_ORDER) {
    summary[role].sort((a, b) => b.value - a.value || a.muscleGroup.localeCompare(b.muscleGroup));
  }

  return ACTIVATION_ROLE_ORDER.some((role) => summary[role].length > 0) ? summary : null;
}

/**
 * Texto da linha, no formato pedido pelo usuário:
 * "Costas: 5,0/10,0 | Bíceps: 2,5/5,0 | Estabilizadores: Core: 1,2/2,0 - Lombar: 1,0/3,0".
 * Principal(is) | secundários | "Estabilizadores:"; dentro de cada papel os
 * itens são separados por " - ".
 */
export function formatItemActivationLine(summary: ItemActivationSummary | null): string {
  if (!summary) return '';
  const join = (entries: ItemActivationEntry[]) => entries.map((e) => e.text).join(' - ');
  const segments: string[] = [];
  if (summary.primary.length) segments.push(join(summary.primary));
  if (summary.secondary.length) segments.push(join(summary.secondary));
  if (summary.stabilizer.length) segments.push(`Estabilizadores: ${join(summary.stabilizer)}`);
  return segments.join(' | ');
}

export function itemActivationKey(slotSequence: number, itemIndex: number): string {
  return `${slotSequence}:${itemIndex}`;
}

/**
 * Casa a resposta do preview com as linhas do builder. Chave de saída:
 * `itemActivationKey(slot.sequence, índice do item em slot.items)` — índice
 * no ESTADO do builder. O servidor devolve `item_index` no corpo ENVIADO, que
 * só leva os itens que contribuem (`isActivationContributingItem`); aqui o
 * mesmo filtro refaz a correspondência.
 *
 * Com `keepPreviousData`, a resposta pode ser de um corpo anterior enquanto o
 * novo carrega. Se só as séries mudaram, a linha mantém o valor anterior (como
 * a tabela). Se a ESTRUTURA mudou (item removido/reordenado/trocado), o eco de
 * identidade (`item_kind` + `mapping_id`/`muscle_group_id`) não bate e a linha
 * fica vazia até a resposta nova chegar — nunca mostra o número de outro item.
 */
export function buildItemActivationLookup(
  slots: WorkoutPlanTemplateSlotPayload[],
  response: PreviewActivationResponse | undefined,
): Map<string, ItemActivationSummary> {
  const lookup = new Map<string, ItemActivationSummary>();
  if (!response?.items?.length || !response.group_totals) return lookup;
  const groupTotals = response.group_totals;

  forEachMatchedPreviewItem(slots, response, ({ slot, stateIndex, echoed }) => {
    const summary = summarizeItemActivation(echoed.contributions, groupTotals);
    if (summary) lookup.set(itemActivationKey(slot.sequence, stateIndex), summary);
  });
  return lookup;
}

export type MatchedPreviewItem = {
  slot: WorkoutPlanTemplateSlotPayload;
  item: WorkoutPlanTemplateSlotItemPayload;
  /** Índice do item em `slot.items` (estado do builder). */
  stateIndex: number;
  echoed: PreviewActivationItem;
};

/**
 * Percorre os itens do builder que têm eco em `response.items[]`, refazendo o
 * filtro de `isActivationContributingItem` (o `item_index` do servidor é a
 * posição no corpo ENVIADO) e conferindo a identidade (`item_kind` +
 * `mapping_id`/`muscle_group_id`) — resposta de um corpo anterior
 * (`keepPreviousData`) com estrutura diferente não casa com o item errado.
 */
export function forEachMatchedPreviewItem(
  slots: WorkoutPlanTemplateSlotPayload[],
  response: PreviewActivationResponse | null | undefined,
  visit: (match: MatchedPreviewItem) => void,
): void {
  forEachMatchedPreviewEcho(slots, response?.items, visit);
}

/** Identidade que toda lista por item do preview ecoa (`items[]`,
 * `item_prior_fatigue[]`): chave do corpo + o que o item é. */
export type PreviewItemEcho = Pick<
  PreviewActivationItem,
  'slot_sequence' | 'item_index' | 'item_kind' | 'mapping_id' | 'muscle_group_id'
>;

/** A mesma correspondência de `forEachMatchedPreviewItem` para qualquer lista
 * por item do preview que traga o eco de identidade. */
export function forEachMatchedPreviewEcho<T extends PreviewItemEcho>(
  slots: WorkoutPlanTemplateSlotPayload[],
  echoes: ReadonlyArray<T> | null | undefined,
  visit: (match: {
    slot: WorkoutPlanTemplateSlotPayload;
    item: WorkoutPlanTemplateSlotItemPayload;
    stateIndex: number;
    echoed: T;
  }) => void,
): void {
  if (!echoes?.length) return;

  const byKey = new Map(echoes.map((echo) => [itemActivationKey(echo.slot_sequence, echo.item_index), echo]));

  for (const slot of slots) {
    let payloadIndex = 0;
    slot.items.forEach((item, stateIndex) => {
      if (!isActivationContributingItem(slot, item)) return;
      const echoed = byKey.get(itemActivationKey(slot.sequence, payloadIndex));
      payloadIndex += 1;
      if (!echoed || echoed.item_kind !== item.item_kind) return;
      const sameIdentity =
        item.item_kind === 'muscle_group'
          ? echoed.muscle_group_id === item.muscle_group_id
          : echoed.mapping_id === item.mapping_id;
      if (!sameIdentity) return;
      visit({ slot, item, stateIndex, echoed });
    });
  }
}
