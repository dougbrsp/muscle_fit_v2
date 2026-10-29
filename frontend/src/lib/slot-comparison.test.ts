import { describe, expect, it } from 'vitest';
import { slotComparisonCaption, slotSetsRows, slotVolumeRows } from './slot-comparison';
import type { SlotComparisonResponse } from './training-volume-types';

const slot = { template_id: 7, slot_sequence: 2, slot_id: 31, slot_name: 'Dia B', template_name: 'ABC', label: 'Dia B' };
const group = { key: 'grupo:Peitoral', label: 'Peitoral', order: null };

function response(): SlotComparisonResponse {
  const level = {
    volume: [
      {
        group,
        comparison_status: 'same_slot' as const,
        slot,
        by_period: {
          t0: { activity_id: 9, local_date: '2026-10-20', volume_kg: 1000, avg_rpe: 8, sets_count: 4 },
          t1: { activity_id: 4, local_date: '2026-10-13', volume_kg: 900, avg_rpe: null, sets_count: 3 },
          t2: null,
          t3: null,
        },
      },
    ],
    equivalent_sets: [
      {
        group,
        comparison_status: 'no_slot' as const,
        slot: null,
        by_period: { t0: { activity_id: 9, local_date: '2026-10-20', equivalent_sets: 4.5 }, t1: null, t2: null, t3: null },
      },
    ],
  };
  return {
    limit: 400,
    sessions_per_slot: 4,
    period_keys: ['t0', 't1', 't2', 't3'],
    levels: { volume_class: level, parent_group: level, muscle_group: level },
  };
}

describe('slot-comparison', () => {
  it('converte as linhas de volume sem inventar colunas', () => {
    const [row] = slotVolumeRows(response(), 'muscle_group');
    expect(row.slot?.slot_sequence).toBe(2);
    expect(row.comparisonStatus).toBe('same_slot');
    expect(row.byPeriod.t0).toEqual({ activity_id: 9, local_date: '2026-10-20', volume_kg: 1000, avg_rpe: 8 });
    expect(row.byPeriod.t1?.activity_id).toBe(4);
    expect(row.byPeriod.t2).toBeNull();
    expect(row.byPeriod.t3).toBeNull();
  });

  it('converte séries equivalentes', () => {
    const [row] = slotSetsRows(response(), 'volume_class');
    expect(row.byPeriod.t0).toEqual({ sets_count: 4.5 });
    expect(row.comparisonStatus).toBe('no_slot');
  });

  it('sem resposta = sem linhas', () => {
    expect(slotVolumeRows(undefined, 'muscle_group')).toEqual([]);
    expect(slotSetsRows(undefined, 'parent_group')).toEqual([]);
  });

  it('legenda mostra o rótulo do slot ou a falta de histórico', () => {
    expect(slotComparisonCaption({ slot, comparisonStatus: 'same_slot' })).toBe('Slot: ABC · Dia B');
    expect(slotComparisonCaption({ slot: null, comparisonStatus: 'no_slot' })).toBe(
      'Sem histórico no mesmo slot (T-0 sem Modelo)',
    );
  });
});
