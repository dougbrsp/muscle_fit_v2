import { describe, expect, it } from 'vitest';
import {
  gymEquipmentManufacturerBody,
  gymEquipmentManufacturerPath,
  manufacturerLabel,
} from './gym-equipment-manufacturer';

describe('fabricante por academia (#188)', () => {
  it('monta o caminho do PATCH pelo id do vínculo', () => {
    expect(gymEquipmentManufacturerPath(42)).toBe('/gym-equipment-catalog/42');
  });

  it('apara o texto e manda null quando vazio (volta ao padrão)', () => {
    expect(gymEquipmentManufacturerBody('  Hammer  ')).toEqual({ manufacturer: 'Hammer' });
    expect(gymEquipmentManufacturerBody('   ')).toEqual({ manufacturer: null });
  });

  it('indica quando o valor é o padrão do catálogo', () => {
    expect(manufacturerLabel('Matrix', 'gym')).toBe('Matrix');
    expect(manufacturerLabel('Matrix', 'catalog')).toBe('Matrix (padrão do catálogo)');
    expect(manufacturerLabel(null, null)).toBeNull();
    expect(manufacturerLabel(undefined, undefined)).toBeNull();
  });
});
