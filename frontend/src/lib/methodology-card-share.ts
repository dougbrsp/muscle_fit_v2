import { statusVisual, type StatusVisual } from '@/lib/dashboard-status';
import { forEachMatchedPreviewEcho, itemActivationKey } from '@/lib/item-activation-summary';
import {
  buildOverallView,
  formatRange,
  formatSets,
  STATUS_CONFLICT_TEXT,
  TECHNIQUE_EQUIVALENT_HINT,
} from '@/lib/methodology-volume';
import { plannedEquivalent, type TechniqueCatalog } from '@/lib/technique-accounting';
import type {
  MethodologyGroupEvaluation,
  MethodologyItemDirectShare,
  MethodologyVolumeEvaluation,
  MethodologyVolumeStatus,
} from '@/lib/methodology-volume-types';
import { setsUnitOf } from '@/lib/volume-counting-mode';
import type { VolumeMode, WorkoutPlanTemplateSlotPayload } from '@/lib/workout-plan-templates-types';

/**
 * M43 — participação de cada card do builder (Modo B) por bloco, abaixo da
 * linha de ativação ponderada (M11). Formato pedido pelo usuário em
 * 2026-09-27: `Quadríceps: 3/9/12 <ícone>` = séries deste exercício / séries
 * do grupo neste dia / séries do grupo no Modelo (ciclo).
 *
 * Modo de contagem, Lote 3 (2026-10-16): NADA é calculado aqui. Os três
 * números vêm do servidor
 * (`POST /workout-plan-templates/methodology-volume-preview`), já no modo de
 * contagem do Modelo (`counting_mode`): a fatia do card é
 * `item_shares[].groups[].direct_sets` (séries + equivalente planejado da
 * técnica, × o peso do modo — 1 em "Só primários", a participação cadastrada
 * no ponderado), o dia é `sessions[].direct_sets` e o ciclo,
 * `direct_sets_cycle_total` do bloco. Antes o card refazia a conta pelos
 * grupos PRIMARY da linha de ativação (espelho removido).
 *
 * Regras de exibição:
 * - só Modo B (`equipment_series`); em Modo A devolve lista vazia;
 * - os blocos são os que o motor contou para o item no modo do Modelo
 *   (no ponderado, o secundário aparece marcado como tal);
 * - fatia mostra o NOME e os números do bloco, com a fatia entre parênteses;
 * - o ícone é o status COMBINADO do bloco no Modelo inteiro (`status`);
 *   `NOT_EVALUABLE` sai sem ícone e com o motivo no tooltip;
 * - nada é inventado: sem fatia, sem bloco ou sem sessão do dia, sem linha.
 */

export type ItemPrimaryGroupRef = {
  muscleGroupId: number | null;
  muscleGroup: string;
};

export type ItemDirectSetsShareLine = {
  /** Id do bloco avaliado (chave de render). */
  key: number;
  /** Nome do bloco (o pai, quando o grupo do card é fatia). */
  groupName: string;
  /** Nome da fatia quando difere do bloco; null quando é o próprio grupo. */
  sliceName: string | null;
  /** O item conta no bloco como secundário (só no modo ponderado). */
  isSecondary: boolean;
  cardSets: number;
  slotSets: number;
  cycleSets: number;
  /** "Quadríceps: 3/9/12", "Peitoral (Peitoral superior): 3/9/12" ou "Bíceps (sec. ×0,5): 1/4/8". */
  text: string;
  statusCode: MethodologyVolumeStatus;
  /** Visual do domínio unificado; null em `NOT_EVALUABLE` (sem ícone). */
  status: StatusVisual | null;
  /** Desvio esperado pela intenção declarada (severity informational). */
  isInformational: boolean;
  /** Tooltip/`title` explicando as três parcelas e a faixa por sessão. */
  title: string;
  /** `aria-label` do ícone (ou do texto quando não há ícone). */
  ariaLabel: string;
};

export type ItemTechniqueInput = {
  /** Código da técnica do item (null = sem técnica). */
  code: string | null | undefined;
  applications: number | null | undefined;
  /** Catálogo `code -> coeficientes` (`techniqueCatalogFromItems`). */
  catalog: TechniqueCatalog;
  /** Nome exibido ("Rest-pause"); cai no código. */
  label?: string | null;
};

export type ItemDirectSetsShareInput = {
  volumeMode: VolumeMode | undefined;
  /** `sequence` do slot (1-based, mesmo do payload). */
  slotSequence: number;
  /** Fatia do item no payload (`buildItemDirectShareLookup`); ausente = sem linha. */
  share: MethodologyItemDirectShare | null | undefined;
  evaluation: MethodologyVolumeEvaluation | null | undefined;
  /** "4 séries + 0,8 equiv. (rest-pause ×2)" do card, só para o tooltip. */
  techniqueNote?: string | null;
};

/** Bloco avaliado do grupo: o próprio grupo ou o bloco-pai de que ele é fatia. */
export function findMethodologyBlock(
  groups: MethodologyGroupEvaluation[],
  ref: ItemPrimaryGroupRef,
): MethodologyGroupEvaluation | undefined {
  if (ref.muscleGroupId != null) {
    const id = ref.muscleGroupId;
    return groups.find((group) => group.muscle_group_id === id || group.member_group_ids.includes(id));
  }
  return groups.find((group) => group.muscle_group_name === ref.muscleGroup);
}

export function formatDirectSetsShareText(
  groupName: string,
  sliceName: string | null,
  cardSets: number,
  slotSets: number,
  cycleSets: number,
  secondaryWeight: number | null = null,
): string {
  const qualifiers = [sliceName, secondaryWeight != null ? `sec. ×${formatSets(secondaryWeight)}` : null].filter(
    Boolean,
  );
  const name = qualifiers.length ? `${groupName} (${qualifiers.join(', ')})` : groupName;
  return `${name}: ${formatSets(cardSets)}/${formatSets(slotSets)}/${formatSets(cycleSets)}`;
}

/** Equivalente PLANEJADO da técnica do item (0 sem técnica ou sem séries) — só para a nota do card. */
export function itemTechniqueEquivalent(setsCount: number | null | undefined, technique?: ItemTechniqueInput | null): number {
  const sets = Math.max(0, Math.trunc(setsCount ?? 0));
  if (!technique?.code || sets <= 0) return 0;
  return plannedEquivalent(technique.code, technique.applications, technique.catalog);
}

/**
 * "4 séries + 0,8 equiv. (rest-pause ×2)" — o volume planejado do card com a
 * parcela da técnica escrita à parte. null quando a técnica não soma nada
 * (sem técnica, sem aplicações, FST-7/GVT).
 */
export function formatItemPlannedSetsNote(setsCount: number | null | undefined, technique?: ItemTechniqueInput | null): string | null {
  const equivalent = itemTechniqueEquivalent(setsCount, technique);
  if (equivalent <= 0 || !technique?.code) return null;
  const sets = Math.max(0, Math.trunc(setsCount ?? 0));
  const name = (technique.label || technique.code).toLowerCase();
  return `${sets} ${sets === 1 ? 'série' : 'séries'} + ${formatSets(equivalent)} equiv. (${name} ×${technique.applications})`;
}

/**
 * Casa `item_shares[]` do payload com os cards do builder. Chave:
 * `itemActivationKey(slot.sequence, índice do item em slot.items)` — o mesmo
 * eco de identidade das listas por item do preview de ativação
 * (`forEachMatchedPreviewEcho`): resposta de um corpo anterior com estrutura
 * diferente não casa com o item errado.
 */
export function buildItemDirectShareLookup(
  slots: WorkoutPlanTemplateSlotPayload[],
  evaluation: MethodologyVolumeEvaluation | null | undefined,
): Map<string, MethodologyItemDirectShare> {
  const lookup = new Map<string, MethodologyItemDirectShare>();
  forEachMatchedPreviewEcho(slots, evaluation?.item_shares, ({ slot, stateIndex, echoed }) => {
    lookup.set(itemActivationKey(slot.sequence, stateIndex), echoed);
  });
  return lookup;
}

export function buildItemDirectSetsShareLines(input: ItemDirectSetsShareInput): ItemDirectSetsShareLine[] {
  const { volumeMode, slotSequence, share, evaluation, techniqueNote = null } = input;
  if (volumeMode !== 'equipment_series' || !evaluation?.groups?.length || !share?.groups?.length) return [];

  const unit = setsUnitOf(evaluation.counting_mode).plural;
  const lines: ItemDirectSetsShareLine[] = [];

  for (const part of share.groups) {
    const block = evaluation.groups.find((group) => group.muscle_group_id === part.muscle_group_id);
    if (!block) continue;
    const session = (block.sessions ?? []).find((entry) => entry.slot_sequence === slotSequence);
    // Resposta anterior (keepPreviousData) ou item sem séries: sem sessão no
    // bloco para este dia, não há denominador honesto — nada a mostrar.
    if (!session) continue;

    const sliceName = part.slice_group_name ?? null;
    const isSecondary = !part.is_primary;
    const cardSets = part.direct_sets;
    const slotSets = session.direct_sets;
    const cycleSets = block.direct_sets_cycle_total;
    const overall = buildOverallView(block);
    const isNotEvaluable = overall.statusCode === 'NOT_EVALUABLE';
    const status = isNotEvaluable ? null : statusVisual('methodologyVolume', overall.statusCode);
    const isInformational = overall.isInformational;
    const setsAxis = block.direct_sets_axis;
    const weeklyAxis = block.weekly_sets_axis;
    const rangeParts: string[] = [];
    const sessionRange = formatRange(setsAxis.range_min, setsAxis.range_max);
    if (sessionRange !== '—') rangeParts.push(`faixa ${sessionRange} por sessão`);
    const weeklyRange = weeklyAxis ? formatRange(weeklyAxis.range_min, weeklyAxis.range_max) : '—';
    if (weeklyRange !== '—') rangeParts.push(`meta ${weeklyRange} por semana`);
    const text = formatDirectSetsShareText(
      block.muscle_group_name,
      sliceName,
      cardSets,
      slotSets,
      cycleSets,
      isSecondary ? part.weight : null,
    );

    const titleParts = [
      `${unit.charAt(0).toUpperCase()}${unit.slice(1)} de ${block.muscle_group_name}: ${formatSets(
        cardSets,
      )} neste exercício${techniqueNote ? ` (${techniqueNote})` : ''} / ${formatSets(slotSets)} neste dia / ${formatSets(
        cycleSets,
      )} no Modelo (ciclo).`,
    ];
    if (isSecondary) {
      titleParts.push(
        `Secundário: conta pela participação cadastrada (×${formatSets(part.weight)}), sem somar exercício.`,
      );
    }
    if (techniqueNote || (block.technique_equivalent_cycle_total ?? 0) > 0) titleParts.push(TECHNIQUE_EQUIVALENT_HINT);
    if (sliceName) titleParts.push(`${sliceName} é fatia de ${block.muscle_group_name}; os números são do bloco.`);
    const reasonText = (overall.reasonLabel ?? 'motivo não informado').toLowerCase();
    if (isNotEvaluable) {
      titleParts.push(`Sem status: ${reasonText}.`);
    } else if (status) {
      titleParts.push(
        `${status.label}${rangeParts.length ? ` (${rangeParts.join('; ')})` : ''}${
          isInformational ? ' — desvio esperado pela intenção declarada' : ''
        }.`,
      );
      if (overall.conflict) titleParts.push(STATUS_CONFLICT_TEXT);
    }

    const ariaLabel = isNotEvaluable
      ? `${block.muscle_group_name}: sem status, ${reasonText}`
      : `${block.muscle_group_name}: ${status?.label ?? overall.statusCode}${isInformational ? ' (esperado)' : ''}${
          overall.conflict ? ', eixos em conflito' : ''
        }`;

    lines.push({
      key: block.muscle_group_id,
      groupName: block.muscle_group_name,
      sliceName,
      isSecondary,
      cardSets,
      slotSets,
      cycleSets,
      text,
      statusCode: overall.statusCode,
      status,
      isInformational,
      title: titleParts.join(' '),
      ariaLabel,
    });
  }

  return lines;
}
