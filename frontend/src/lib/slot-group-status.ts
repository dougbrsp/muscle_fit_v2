// Indicador de planejamento por grupo no slot RECOLHIDO do editor de Modelo
// (`/modelos-treino/[id]`): junta as linhas "Grupo — N séries/M exercícios"
// (`slotGroupSets`, por grupo PRINCIPAL) com o MESMO status que o card de item
// mostra na linha "Séries diretas" (`buildItemDirectSetsShareLines`, M43).
//
// As SÉRIES da linha (M76 E5) também vêm do preview: `direct_sets` da sessão
// do bloco neste dia (PRIMARY, com o equivalente da técnica) — o mesmo
// primeiro número do "9/12". Antes eram a soma de `sets_count` dos itens pelo
// grupo do item, sem técnica, e podiam divergir do "9/12" na mesma linha. Sem
// sessão do bloco neste dia (preview carregando, exercício sem PRIMARY no
// grupo), a linha mostra só os exercícios.
//
// Nada é calculado aqui: o status é o COMBINADO do bloco no Modelo inteiro
// (`buildOverallView` sobre o grupo do preview
// `POST /workout-plan-templates/methodology-volume-preview`), com o visual do
// domínio unificado `methodologyVolume` (`statusVisual`). As mesmas regras do
// card valem:
// - só Modo B (`equipment_series`) — o card de Modo A não exibe status;
// - grupo fatia usa o bloco-pai (`findMethodologyBlock`);
// - sem sessão do bloco para este dia (resposta anterior, ainda recalculando),
//   não há número honesto: sem indicador;
// - `NOT_EVALUABLE` (e grupo sem bloco, sem preview, cardio): sem indicador.
//
// Agregação por grupo principal: o motor resolve fatia -> bloco pela MESMA
// cadeia `methodology_parent_group_id` até a raiz, então o principal da linha
// É o bloco avaliado e todos os subgrupos caem nele (um status só). Se a
// avaliação estiver defasada da lista de grupos (ex.: admin mudou o pai e o
// preview ainda não recalculou), os grupos da linha podem cair em blocos
// diferentes: aí vale o PIOR estado entre eles, para nenhum aviso sumir.

import { statusVisual, type StatusVisual } from './dashboard-status';
import { findMethodologyBlock } from './methodology-card-share';
import { buildOverallView, formatSets } from './methodology-volume';
import type {
  MethodologyGroupEvaluation,
  MethodologyVolumeEvaluation,
  MethodologyVolumeStatus,
} from './methodology-volume-types';
import { formatExerciseCount, type SlotGroupSetsRow } from './slot-group-sets';
import { setsUnitOf } from './volume-counting-mode';
import type { VolumeMode } from './workout-plan-templates-types';

export type SlotGroupPlanningStatus = {
  statusCode: MethodologyVolumeStatus;
  status: StatusVisual;
  /** Séries diretas do bloco neste dia e no Modelo (ciclo) — os 2 últimos números do card. */
  slotSets: number;
  cycleSets: number;
  /** "9/12" (dia/ciclo). */
  valueText: string;
  /** Desvio esperado pela intenção declarada. */
  isInformational: boolean;
  /** Tooltip: rótulo do status + números do bloco. */
  title: string;
  /** `aria-label` do ícone: nunca depende só da cor. */
  ariaLabel: string;
};

export type SlotGroupSetsWithStatusRow = SlotGroupSetsRow & {
  /** Séries diretas do grupo neste dia, do preview do backend; `null` sem sessão. */
  sets: number | null;
  planning: SlotGroupPlanningStatus | null;
};

/** "9 séries/3 exercícios" (séries do backend) ou só "3 exercícios". */
function rowLabel(sets: number | null, exercises: number): string {
  const exercisesLabel = formatExerciseCount(exercises);
  if (sets == null) return exercisesLabel;
  return `${formatSets(sets)} ${sets === 1 ? 'série' : 'séries'}/${exercisesLabel}`;
}

export type SlotGroupStatusInput = {
  rows: ReadonlyArray<SlotGroupSetsRow>;
  volumeMode: VolumeMode | undefined;
  /** `sequence` do slot (1-based, mesmo do payload). */
  slotSequence: number;
  evaluation: MethodologyVolumeEvaluation | null | undefined;
};

/**
 * Gravidade para escolher o pior estado entre blocos: desvio real (abaixo /
 * acima) > não treinado diretamente > desvio esperado pela intenção > na faixa.
 */
function severityRank(planning: SlotGroupPlanningStatus): number {
  if (planning.statusCode === 'BELOW_RANGE' || planning.statusCode === 'ABOVE_RANGE') {
    return planning.isInformational ? 1 : 3;
  }
  if (planning.statusCode === 'NOT_DIRECTLY_TRAINED') return 2;
  return 0;
}

function blockPlanning(
  row: SlotGroupSetsRow,
  block: MethodologyGroupEvaluation,
  slotSequence: number,
  setsUnit: string,
): SlotGroupPlanningStatus | null {
  const session = block.sessions?.find((entry) => entry.slot_sequence === slotSequence);
  if (!session) return null;

  const overall = buildOverallView(block);
  if (overall.statusCode === 'NOT_EVALUABLE') return null;

  const status = statusVisual('methodologyVolume', overall.statusCode);
  const slotSets = session.direct_sets;
  const cycleSets = block.direct_sets_cycle_total;
  const valueText = `${formatSets(slotSets)}/${formatSets(cycleSets)}`;
  const isSlice = block.muscle_group_id !== row.muscleGroupId;
  const blockNote = isSlice ? ` (bloco ${block.muscle_group_name})` : '';
  const expected = overall.isInformational ? ' — desvio esperado pela intenção declarada' : '';
  const title =
    `${status.label}${expected}: ${formatSets(slotSets)} ${setsUnit} neste dia / ` +
    `${formatSets(cycleSets)} no Modelo (ciclo)${blockNote}.`;
  const ariaLabel = `${row.name}: ${status.label}${overall.isInformational ? ' (esperado)' : ''}, ${valueText}`;

  return {
    statusCode: overall.statusCode,
    status,
    slotSets,
    cycleSets,
    valueText,
    isInformational: overall.isInformational,
    title,
    ariaLabel,
  };
}

export function slotGroupSetsWithStatus(input: SlotGroupStatusInput): SlotGroupSetsWithStatusRow[] {
  const { rows, volumeMode, slotSequence, evaluation } = input;
  const groups = volumeMode === 'equipment_series' ? (evaluation?.groups ?? []) : [];

  const result = rows.map((row): SlotGroupSetsWithStatusRow => {
    if (groups.length === 0) return { ...row, sets: null, label: rowLabel(null, row.exercises), planning: null };

    // Principal primeiro: no caso normal ele já é o bloco e decide sozinho.
    const blocks: MethodologyGroupEvaluation[] = [];
    for (const groupId of [row.muscleGroupId, ...(row.memberGroupIds ?? [])]) {
      const block = findMethodologyBlock(groups, { muscleGroupId: groupId, muscleGroup: row.name });
      if (block && !blocks.includes(block)) blocks.push(block);
    }

    let sets: number | null = null;
    let planning: SlotGroupPlanningStatus | null = null;
    for (const block of blocks) {
      const session = block.sessions?.find((entry) => entry.slot_sequence === slotSequence);
      if (session) sets = (sets ?? 0) + session.direct_sets;
      const candidate = blockPlanning(row, block, slotSequence, setsUnitOf(evaluation?.counting_mode).plural);
      if (candidate && (!planning || severityRank(candidate) > severityRank(planning))) planning = candidate;
    }
    return { ...row, sets, label: rowLabel(sets, row.exercises), planning };
  });

  // Mais séries primeiro (sem número por último); empate: nome (pt-BR).
  return result.sort((a, b) => (b.sets ?? -1) - (a.sets ?? -1) || a.name.localeCompare(b.name, 'pt-BR'));
}
