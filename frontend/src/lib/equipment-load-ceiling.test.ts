import { describe, expect, it } from 'vitest';
import { equipmentLoadCeilingPath, equipmentLoadCeilingQueryKey, stepUpAllowed } from './equipment-load-ceiling';
import { fromKg, loadStepperStep } from './weight-units';

describe('equipment-load-ceiling', () => {
  it('monta o caminho do par e a chave de cache', () => {
    const pair = { mappingId: 5, gymEquipmentId: 9 };
    expect(equipmentLoadCeilingPath(pair)).toBe('/me/equipment-load-ceiling?mapping_id=5&gym_equipment_id=9');
    expect(equipmentLoadCeilingQueryKey(pair)).toEqual(['equipment-load-ceiling', 5, 9]);
    expect(equipmentLoadCeilingQueryKey(null)).toEqual(['equipment-load-ceiling', null, null]);
  });

  it('sem limite do servidor o "+" sempre pode', () => {
    expect(stepUpAllowed(500, 2.5, null)).toBe(true);
    expect(stepUpAllowed(null, 2.5, undefined)).toBe(true);
  });

  it('kg: o "+" só habilita se o próximo passo inteiro cabe no limite', () => {
    expect(stepUpAllowed(97.5, 2.5, 100)).toBe(true); // 100 <= 100
    expect(stepUpAllowed(100, 2.5, 100)).toBe(false);
    expect(stepUpAllowed(99, 2.5, 100)).toBe(false); // 101,5 passaria do teto
    expect(stepUpAllowed(null, 2.5, 100)).toBe(true);
  });

  it('lb: o limite do servidor é convertido e o passo da unidade é respeitado', () => {
    const limit = fromKg(100, 'lb'); // 220,5 lb
    const step = loadStepperStep('lb', 2.5);
    expect(stepUpAllowed(limit - step, step, limit)).toBe(true);
    expect(stepUpAllowed(limit - step + 0.5, step, limit)).toBe(false);
    expect(stepUpAllowed(limit, step, limit)).toBe(false);
  });
});
