/**
 * Adaptador do comparativo T-0..T-3 por slot (`GET /analytics/muscle-groups-slot-comparison`)
 * para as linhas das tabelas e gráficos de `/volume-grupamentos`.
 *
 * Nenhuma regra mora aqui: quais sessões entram em cada coluna (T-0 = última
 * sessão; T-1..T-3 = anteriores no MESMO slot do Modelo, por `template_id` +
 * `slot_sequence`) e a soma por macro-grupo/grupo pai são do backend
 * (`api/app/intelligence/slot_session_comparison.py`, mapa FONTES_DA_VERDADE §3.5).
 * Esta lib só troca o formato (snake_case da API → linhas da tela).
 */

import type {
  SlotComparativeSetsRow,
  SlotComparativeVolumeRow,
  SlotComparisonResponse,
  WorkoutPeriodKey,
} from './training-volume-types';
import type { TrainingGoalValue } from './dashboard-v3-types';
import type { VolumeGroupingLevel } from './volume-grouping';

const PERIOD_KEYS: WorkoutPeriodKey[] = ['t0', 't1', 't2', 't3'];

/**
 * Caminho do comparativo por slot. M97 F4: `goal` opcional filtra os
 * exercícios do objetivo (sem ele, todos — a resposta de sempre); as séries
 * equivalentes já chegam com a série de força ponderada pelo motor de volume.
 */
export function slotComparisonPath(limit: number, goal?: TrainingGoalValue | null): string {
  const search = new URLSearchParams({ limit: String(limit) });
  if (goal) search.set('goal', goal);
  return `/analytics/muscle-groups-slot-comparison?${search.toString()}`;
}

export function slotVolumeRows(
  response: SlotComparisonResponse | undefined,
  level: VolumeGroupingLevel,
): SlotComparativeVolumeRow[] {
  const rows = response?.levels?.[level]?.volume ?? [];
  return rows.map((row) => {
    const byPeriod = {} as SlotComparativeVolumeRow['byPeriod'];
    for (const key of PERIOD_KEYS) {
      const period = row.by_period?.[key] ?? null;
      byPeriod[key] = period
        ? {
            activity_id: period.activity_id,
            local_date: period.local_date,
            volume_kg: period.volume_kg,
            avg_rpe: period.avg_rpe,
          }
        : null;
    }
    return { group: row.group, slot: row.slot, comparisonStatus: row.comparison_status, byPeriod };
  });
}

export function slotSetsRows(
  response: SlotComparisonResponse | undefined,
  level: VolumeGroupingLevel,
): SlotComparativeSetsRow[] {
  const rows = response?.levels?.[level]?.equivalent_sets ?? [];
  return rows.map((row) => {
    const byPeriod = {} as SlotComparativeSetsRow['byPeriod'];
    for (const key of PERIOD_KEYS) {
      const period = row.by_period?.[key] ?? null;
      byPeriod[key] = period ? { sets_count: period.equivalent_sets } : null;
    }
    return { group: row.group, slot: row.slot, comparisonStatus: row.comparison_status, byPeriod };
  });
}

/** Texto curto do slot comparado (só exibição; a chave é o ID). */
export function slotComparisonCaption(row: Pick<SlotComparativeVolumeRow, 'slot' | 'comparisonStatus'>): string {
  if (row.comparisonStatus === 'no_slot' || !row.slot) return 'Sem histórico no mesmo slot (T-0 sem Modelo)';
  const template = row.slot.template_name ? `${row.slot.template_name} · ` : '';
  return `Slot: ${template}${row.slot.label}`;
}
