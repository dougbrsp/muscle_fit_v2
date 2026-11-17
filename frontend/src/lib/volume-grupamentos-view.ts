/**
 * Mapeamentos de APRESENTAÇÃO de `/volume-grupamentos`: direção pronta do
 * backend -> cor/rótulo, nome de equipamento -> rótulo, linhas de equipamento
 * achatadas para a tabela. Nenhuma comparação numérica aqui: deltas, direções,
 * janelas e ordem vêm de `intelligence/slot_session_comparison.py`.
 */
import { EXERCISE_PROGRESSION_LABELS } from './dashboard-status';
import type {
  ApiTrendDirection,
  EquipmentWeekMetrics,
  EquipmentWeeklyGroupRow,
  TrainingVolumeGranularity,
  WorkoutPeriodKey,
} from './training-volume-types';
import type { VolumeGroup } from './volume-grouping';

/** Selo de leitura do volume no período (o rótulo de estável vem do espelho gerado do backend). */
export const TREND_BADGE: Record<'up' | 'down' | 'stable', { color: string; label: string }> = {
  up: { color: 'green', label: 'Aumentou' },
  down: { color: 'red', label: 'Diminuiu' },
  stable: { color: 'gray', label: EXERCISE_PROGRESSION_LABELS.STABLE },
};

export function trendBadge(direction: ApiTrendDirection | undefined): { color: string; label: string } {
  return direction ? TREND_BADGE[direction] : { color: 'gray', label: '-' };
}

/** Nomes das janelas por granularidade (as datas vêm de `period_comparison`). */
export const GRANULARITY_WINDOW_LABELS: Record<
  TrainingVolumeGranularity,
  { title: string; currentLabel: string; previousLabel: string }
> = {
  d7: { title: 'D-7', currentLabel: 'D-7 atual', previousLabel: 'D-7 anterior' },
  week: { title: 'Semana x Semana', currentLabel: 'Semana atual', previousLabel: 'Semana anterior' },
  month: { title: 'Mês x Mês', currentLabel: 'Mês atual', previousLabel: 'Mês anterior' },
};

export const NO_EQUIPMENT_LABEL = 'Sem equipamento';

export type EquipmentDisplayRow = {
  group: VolumeGroup;
  label: string;
  isTotal: boolean;
  isFirstOfGroup: boolean;
  groupSize: number;
  byPeriod: Partial<Record<WorkoutPeriodKey, EquipmentWeekMetrics | null>>;
};

/** Achata os blocos prontos (Total primeiro, já ordenados) em linhas da tabela. */
export function equipmentDisplayRows(blocks: EquipmentWeeklyGroupRow[] | undefined): EquipmentDisplayRow[] {
  return (blocks ?? []).flatMap(({ group, equipments }) =>
    equipments.map((equipment, index) => ({
      group,
      label: equipment.is_total ? 'Total' : equipment.equipment_name ?? NO_EQUIPMENT_LABEL,
      isTotal: equipment.is_total,
      isFirstOfGroup: index === 0,
      groupSize: equipments.length,
      byPeriod: equipment.by_period,
    })),
  );
}

const SPARKLINE_ORDER: WorkoutPeriodKey[] = ['t3', 't2', 't1', 't0'];

/** Série S/T-3..S/T-0 para a sparkline; período ausente = `null`. */
export function sparklineSeries<T>(
  byPeriod: Partial<Record<WorkoutPeriodKey, T | null>>,
  pick: (period: T) => number | null,
): Array<number | null> {
  return SPARKLINE_ORDER.map((key) => {
    const period = byPeriod[key];
    return period ? pick(period) : null;
  });
}
