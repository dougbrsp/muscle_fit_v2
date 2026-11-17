import { describe, expect, it } from 'vitest';
import { EXERCISE_PROGRESSION_LABELS } from './dashboard-status';
import { equipmentDisplayRows, sparklineSeries, trendBadge } from './volume-grupamentos-view';
import type { EquipmentWeeklyGroupRow } from './training-volume-types';
import type { VolumeGroup } from './volume-grouping';

const group = { key: 'peito', label: 'Peito' } as VolumeGroup;

describe('apresentação de /volume-grupamentos', () => {
  it('selo segue a direção pronta; estável lê o rótulo do espelho gerado', () => {
    expect(trendBadge('up')).toEqual({ color: 'green', label: 'Aumentou' });
    expect(trendBadge('down').color).toBe('red');
    expect(trendBadge('stable').label).toBe(EXERCISE_PROGRESSION_LABELS.STABLE);
    expect(trendBadge(null).label).toBe('-');
  });

  it('equipamentos: Total primeiro, nome nulo vira "Sem equipamento", ordem preservada', () => {
    const blocks: EquipmentWeeklyGroupRow[] = [
      {
        group,
        equipments: [
          { equipment_name: null, is_total: true, by_period: {} },
          { equipment_name: 'Barra', is_total: false, by_period: {} },
          { equipment_name: null, is_total: false, by_period: {} },
        ],
      },
    ];
    const rows = equipmentDisplayRows(blocks);
    expect(rows.map((row) => row.label)).toEqual(['Total', 'Barra', 'Sem equipamento']);
    expect(rows.map((row) => row.isFirstOfGroup)).toEqual([true, false, false]);
    expect(rows[0].groupSize).toBe(3);
    expect(equipmentDisplayRows(undefined)).toEqual([]);
  });

  it('sparkline vai de t3 a t0 com null nos períodos ausentes', () => {
    expect(sparklineSeries({ t0: { v: 5 }, t2: { v: 3 }, t1: null }, (p: { v: number }) => p.v)).toEqual([null, 3, null, 5]);
  });
});
