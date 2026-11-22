import { describe, expect, it } from 'vitest';
import {
  buildEquipmentPickerSections,
  currentLoadConvention,
  needsLoadConventionWarning,
  type EquipmentPickerItem,
} from './equipment-picker';

function item(id: number, mapping_id: number, equipment_name: string, extra: Partial<EquipmentPickerItem> = {}) {
  return { id, mapping_id, equipment_name, gym_id: 1, equipment_id: id, ...extra } as EquipmentPickerItem;
}

const items = [
  item(1, 10, 'Polia', { mapping_exercise_name: 'Crucifixo', load_convention: 'total' }),
  item(2, 11, 'Máquina Peitoral', { mapping_exercise_name: 'Supino', manufacturer: 'Técnica' }),
  item(3, 10, 'Halter', { mapping_exercise_name: 'Crucifixo', load_convention: 'per_dumbbell' }),
];

describe('buildEquipmentPickerSections', () => {
  it('põe o mesmo exercício antes do grupo, exclui o atual e mantém a ordem da API', () => {
    const sections = buildEquipmentPickerSections(items, 10, 1, '');
    expect(sections.map((s) => s.key)).toEqual(['same_exercise', 'same_group']);
    expect(sections[0].items.map((i) => i.id)).toEqual([3]);
    expect(sections[1].items.map((i) => i.id)).toEqual([2]);
  });

  it('busca sem acento nem caixa, por equipamento, exercício e fabricante', () => {
    expect(buildEquipmentPickerSections(items, 10, 1, 'maquina')[0].items[0].id).toBe(2);
    expect(buildEquipmentPickerSections(items, 10, 1, 'SUPINO')[0].items[0].id).toBe(2);
    expect(buildEquipmentPickerSections(items, 10, 1, 'tecnica')[0].items[0].id).toBe(2);
  });

  it('seção vazia some e busca sem resultado devolve lista vazia', () => {
    expect(buildEquipmentPickerSections(items, 10, 1, 'halter').map((s) => s.key)).toEqual(['same_exercise']);
    expect(buildEquipmentPickerSections(items, 10, 1, 'xyz')).toEqual([]);
  });

  it('sem mapping atual tudo cai em mesmo grupo', () => {
    expect(buildEquipmentPickerSections(items, null, null, '').map((s) => s.key)).toEqual(['same_group']);
  });
});

describe('convenção de carga', () => {
  it('lê a convenção do atual na lista', () => {
    expect(currentLoadConvention(items, 1)).toBe('total');
    expect(currentLoadConvention(items, 2)).toBeNull();
    expect(currentLoadConvention(items, null)).toBeNull();
  });

  it('pede aviso só quando diferente e ambas conhecidas', () => {
    expect(needsLoadConventionWarning('per_dumbbell', 'total')).toBe(true);
    expect(needsLoadConventionWarning('total', 'total')).toBe(false);
    expect(needsLoadConventionWarning(undefined, 'total')).toBe(false);
    expect(needsLoadConventionWarning('total', null)).toBe(false);
  });
});
