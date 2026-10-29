import { statusVisual, type StatusVisual } from '@/lib/dashboard-status';
import { formatPerSession, formatRange } from '@/lib/methodology-volume';
import type { MethodologyVolumeEvaluation, MethodologyVolumeStatus } from '@/lib/methodology-volume-types';
import { setsUnitOf } from '@/lib/volume-counting-mode';

/**
 * Fase 7 (Frente A) — janela "Séries por grupo": uma barra por grupo com as
 * séries DIRETAS por semana (`weekly_sets_axis.value_weekly`, Fase 6) contra a
 * meta semanal do macro-grupo (`range_min`/`range_max`).
 *
 * Só entram grupos com macro-grupo (`volume_class_id`), inclusive os não
 * treinados (valor 0). A escala vai de 0 a `máx × 1,5`: faixa abaixo do mínimo,
 * faixa dentro da meta e faixa acima do máximo. Abaixo e dentro usam as cores
 * do domínio `methodologyVolume` de `dashboard-status.ts` (cinza e verde); o
 * trecho acima do máximo é VERMELHO só nesta barra, por pedido explícito do
 * usuário ("cinza abaixo, verde ideal, vermelho acima"). O ícone de status
 * continua na paleta do domínio (laranja acima).
 */

/** A escala da barra estende a meta máxima em 50%. */
export const WEEKLY_SETS_SCALE_FACTOR = 1.5;

export type WeeklySetsBarSegments = {
  /** Larguras em % da barra; somam 100. */
  belowPct: number;
  withinPct: number;
  abovePct: number;
};

export type WeeklySetsBarRow = {
  groupId: number;
  groupName: string;
  /** Id do macro-grupo: segue a ordem do cadastro (Grandes, Pequenos, Outros). */
  classId: number;
  className: string;
  value: number;
  rangeMin: number | null;
  rangeMax: number | null;
  /** `false` = sem meta semanal: barra neutra, sem segmentos nem marcador. */
  hasRange: boolean;
  segments: WeeklySetsBarSegments | null;
  /** Posição do marcador em % (0–100); null sem meta. */
  markerPct: number | null;
  /** `true` quando o valor passa do fim da escala (marcador preso em 100%). */
  overflow: boolean;
  /** "12 / 18–32" ou "12 · sem meta". */
  text: string;
  statusCode: MethodologyVolumeStatus;
  status: StatusVisual;
  ariaLabel: string;
};

/** Vermelho do trecho acima do máximo (red.8 do Mantine), exclusivo desta barra. */
const ABOVE_RED = '#e03131';

/** Cores dos três trechos da barra: abaixo/dentro do domínio, acima vermelho. */
export const WEEKLY_SETS_BAR_COLORS = {
  below: statusVisual('methodologyVolume', 'BELOW_RANGE').hex,
  within: statusVisual('methodologyVolume', 'WITHIN_RANGE').hex,
  above: ABOVE_RED,
} as const;

function round1(value: number): number {
  return Math.round(value * 10) / 10;
}

export function buildWeeklySetsBarRow(
  group: MethodologyVolumeEvaluation['groups'][number],
  countingMode?: string | null,
): WeeklySetsBarRow {
  // Lote 3: a unidade é a do modo de contagem do Modelo (vem do motor).
  const unit = setsUnitOf(countingMode).plural;
  const axis = group.weekly_sets_axis;
  const value = axis?.value_weekly ?? 0;
  const rangeMin = axis?.range_min ?? null;
  const rangeMax = axis?.range_max ?? null;
  const hasRange = rangeMin != null && rangeMax != null && rangeMax > 0;
  const statusCode = axis?.status ?? 'NOT_EVALUABLE';
  const status = statusVisual('methodologyVolume', statusCode);
  const valueText = formatPerSession(value);

  let segments: WeeklySetsBarSegments | null = null;
  let markerPct: number | null = null;
  let overflow = false;
  if (hasRange) {
    const scaleMax = rangeMax * WEEKLY_SETS_SCALE_FACTOR;
    const belowPct = round1((Math.min(rangeMin, rangeMax) / scaleMax) * 100);
    const withinPct = round1(((rangeMax - Math.min(rangeMin, rangeMax)) / scaleMax) * 100);
    segments = { belowPct, withinPct, abovePct: round1(100 - belowPct - withinPct) };
    overflow = value > scaleMax;
    markerPct = round1((Math.min(Math.max(value, 0), scaleMax) / scaleMax) * 100);
  }

  const text = hasRange ? `${valueText} / ${formatRange(rangeMin, rangeMax)}` : `${valueText} · sem meta`;
  const ariaLabel = hasRange
    ? `${group.muscle_group_name}: ${valueText} ${unit} por semana, meta ${formatRange(rangeMin, rangeMax)}; ${status.label}`
    : `${group.muscle_group_name}: ${valueText} ${unit} por semana, sem meta semanal`;

  return {
    groupId: group.muscle_group_id,
    groupName: group.muscle_group_name,
    classId: group.volume_class_id ?? Number.MAX_SAFE_INTEGER,
    className: group.volume_class_name ?? '',
    value,
    rangeMin,
    rangeMax,
    hasRange,
    segments,
    markerPct,
    overflow,
    text,
    statusCode,
    status,
    ariaLabel,
  };
}

/**
 * Grupos com macro-grupo, ordenados pelo id do macro-grupo (ordem do
 * cadastro; o nome daria Grandes, Outros, Pequenos) e depois pelo nome do grupo.
 */
export function buildWeeklySetsBars(evaluation: MethodologyVolumeEvaluation | null | undefined): WeeklySetsBarRow[] {
  if (!evaluation) return [];
  return evaluation.groups
    .filter((group) => group.volume_class_id != null)
    .map((group) => buildWeeklySetsBarRow(group, evaluation.counting_mode))
    .sort(
      (a, b) => a.classId - b.classId || a.groupName.localeCompare(b.groupName, 'pt-BR'),
    );
}
