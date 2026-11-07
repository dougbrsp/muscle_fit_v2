import { describe, expect, it } from 'vitest';
import {
  displayLoadWithinCeiling,
  equipmentLoadCeilingPath,
  equipmentLoadCeilingQueryKey,
} from './equipment-load-ceiling';

describe('equipment-load-ceiling', () => {
  it('monta o caminho do par e a chave de cache', () => {
    const pair = { mappingId: 5, gymEquipmentId: 9 };
    expect(equipmentLoadCeilingPath(pair)).toBe('/me/equipment-load-ceiling?mapping_id=5&gym_equipment_id=9');
    expect(equipmentLoadCeilingQueryKey(pair)).toEqual(['equipment-load-ceiling', 5, 9]);
    expect(equipmentLoadCeilingQueryKey(null)).toEqual(['equipment-load-ceiling', null, null]);
  });

  it('a carga mostrada nunca passa do teto do servidor; sem teto, não muda', () => {
    expect(displayLoadWithinCeiling(110, 100)).toBe(100);
    expect(displayLoadWithinCeiling(90, 100)).toBe(90);
    expect(displayLoadWithinCeiling(110, null)).toBe(110);
    expect(displayLoadWithinCeiling(null, 100)).toBeNull();
  });
});
