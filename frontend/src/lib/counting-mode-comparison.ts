import { statusVisual, type StatusVisual } from '@/lib/dashboard-status';
import { formatRange, formatSets, notEvaluableReasonLabel } from '@/lib/methodology-volume';
import type { CountingModeComparison, CountingModeGroupSnapshot } from '@/lib/methodology-volume-types';
import { FREQUENCY_BUCKET_LABEL } from '@/lib/volume-classes';
import { countingModeOption, DEFAULT_COUNTING_MODE, UNCLASSIFIED_VOLUME_CLASS_LABEL } from '@/lib/volume-counting-mode';

/**
 * Diagnóstico de volume — comparação lado a lado dos modos de contagem
 * (Lote 3). Só FORMATAÇÃO: os números, buckets, status e as transições
 * (`status_changed`/`bucket_changed`) vêm prontos de
 * `GET /workout-plan-templates/{id}/methodology-volume/counting-modes`
 * (motor `methodology_volume.compare_counting_modes`).
 */

export type CountingModeCellView = {
  mode: string;
  modeLabel: string;
  /** "12 séries ponderadas/sem" ou "—" sem sessão que conte. */
  weeklyText: string;
  /** "meta 12–20" ou null sem meta. */
  weeklyRangeText: string | null;
  bucketText: string;
  status: StatusVisual;
  statusLabel: string;
  isCurrent: boolean;
  uncalibrated: boolean;
};

export type CountingModeRowView = {
  key: number;
  groupName: string;
  className: string;
  cells: CountingModeCellView[];
  statusChanged: boolean;
  bucketChanged: boolean;
  /** "Na faixa → Acima da faixa" quando o status muda; null sem transição. */
  transitionText: string | null;
};

export type CountingModeHeaderView = { mode: string; label: string; description: string; isCurrent: boolean };

function cellView(snapshot: CountingModeGroupSnapshot, currentMode: string): CountingModeCellView {
  const option = countingModeOption(snapshot.counting_mode);
  const status = statusVisual('methodologyVolume', snapshot.status);
  const reason = snapshot.status === 'NOT_EVALUABLE' ? notEvaluableReasonLabel(snapshot.status_reason) : null;
  const range = formatRange(snapshot.weekly_range_min, snapshot.weekly_range_max);
  return {
    mode: snapshot.counting_mode,
    modeLabel: option.label,
    weeklyText:
      snapshot.direct_sets_weekly != null
        ? `${formatSets(snapshot.direct_sets_weekly)} ${snapshot.sets_unit_label}/sem`
        : '—',
    weeklyRangeText: range !== '—' ? `meta ${range}` : null,
    bucketText: snapshot.frequency_bucket ? FREQUENCY_BUCKET_LABEL[snapshot.frequency_bucket] : 'sem sessão',
    status,
    statusLabel: reason ? `${status.label} (${reason.toLowerCase()})` : status.label,
    isCurrent: snapshot.counting_mode === currentMode,
    uncalibrated: snapshot.uncalibrated_ranges,
  };
}

export function buildCountingModeHeaders(comparison: CountingModeComparison): CountingModeHeaderView[] {
  return comparison.counting_modes.map((mode) => {
    const option = countingModeOption(mode);
    return {
      mode,
      label: option.label,
      description: option.description,
      isCurrent: mode === comparison.current_counting_mode,
    };
  });
}

export function buildCountingModeRows(comparison: CountingModeComparison): CountingModeRowView[] {
  return comparison.groups.map((group) => {
    const cells = group.modes.map((snapshot) => cellView(snapshot, comparison.current_counting_mode));
    return {
      key: group.muscle_group_id,
      groupName: group.muscle_group_name,
      className: group.volume_class_name || UNCLASSIFIED_VOLUME_CLASS_LABEL,
      cells,
      statusChanged: group.status_changed,
      bucketChanged: group.bucket_changed,
      transitionText: group.status_changed ? cells.map((cell) => cell.status.label).join(' → ') : null,
    };
  });
}

/** Aviso do topo: modos cujas faixas ainda são a cópia das do modo default. */
export function uncalibratedWarning(comparison: CountingModeComparison): string | null {
  if (!comparison.uncalibrated_modes.length) return null;
  const labels = comparison.uncalibrated_modes.map((mode) => `"${countingModeOption(mode).label}"`).join(', ');
  const source = countingModeOption(DEFAULT_COUNTING_MODE).label;
  return `As faixas e metas de ${labels} ainda são a cópia das de "${source}" (não calibradas): o status nesse modo pode marcar acima da faixa só porque passou a contar o secundário. Calibre em Domínio de exercícios › Macro-grupos de volume.`;
}
