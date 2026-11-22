import { describe, expect, it } from 'vitest';
import {
  buildCatalogSection,
  catalogItemState,
  CATALOG_NEEDS_ONLINE_NOTICE,
  CATALOG_NOT_IN_GYM_NOTICE,
  findAddedItem,
  findRecommendedItem,
  formatCatalogAddQuestion,
} from './equipment-catalog-add';
import type { EquipmentExerciseLink } from './equipment-exercises-types';
import type { EquipmentPickerItem } from './equipment-picker';

function pair(over: Partial<EquipmentExerciseLink>): EquipmentExerciseLink {
  return {
    id: 1,
    equipment_id: 30,
    equipment_name: 'Cabo',
    has_photo: false,
    photo_url: null,
    exercise_id: 12,
    exercise_name: 'Crucifixo',
    exercise_amazfit_name: null,
    primary_group: 'Peito',
    secondary_group: null,
    is_preferred: false,
    is_active: true,
    notes: null,
    load_convention: 'total',
    created_at: '',
    updated_at: '',
    ...over,
  };
}

function gymItem(over: Partial<EquipmentPickerItem>): EquipmentPickerItem {
  return {
    id: 900,
    gym_id: 1,
    mapping_id: 12,
    mapping_exercise_name: 'Crucifixo',
    equipment_id: 30,
    equipment_name: 'Cabo',
    ...over,
  };
}

describe('buildCatalogSection', () => {
  const gym = [gymItem({})];

  it('não traz o par que a academia já tem (por id)', () => {
    expect(buildCatalogSection([pair({})], gym, 12, '')).toEqual([]);
  });

  it('não traz o par que a academia já tem com id legado (casa pelo nome)', () => {
    const legacy = [gymItem({ mapping_id: 999, mapping_exercise_name: 'CRUCIFIXO' })];
    expect(buildCatalogSection([pair({})], legacy, 12, '')).toEqual([]);
  });

  it('com exercise_id igual ao do par casa mesmo com nome diferente', () => {
    const item = [gymItem({ mapping_id: 999, exercise_id: 12, mapping_exercise_name: 'Outro nome' })];
    expect(buildCatalogSection([pair({})], item, 12, '')).toEqual([]);
  });

  it('com exercise_id diferente e nome igual NÃO é o mesmo par', () => {
    const item = [gymItem({ mapping_id: 12, exercise_id: 77, mapping_exercise_name: 'Crucifixo' })];
    const p = pair({});
    expect(buildCatalogSection([p], item, 12, '')).toEqual([p]);
  });

  it('sem exercise_id ou com null vale a regra antiga (mapping_id e nome)', () => {
    for (const exercise_id of [undefined, null]) {
      const byId = [gymItem({ exercise_id, mapping_id: 12, mapping_exercise_name: 'Outro' })];
      const byName = [gymItem({ exercise_id, mapping_id: 999, mapping_exercise_name: 'crucifixo' })];
      expect(buildCatalogSection([pair({})], byId, 12, '')).toEqual([]);
      expect(buildCatalogSection([pair({})], byName, 12, '')).toEqual([]);
    }
  });

  it('mesmo equipamento em outro exercício entra', () => {
    const other = pair({ id: 2, exercise_id: 13, exercise_name: 'Peck deck' });
    expect(buildCatalogSection([other], gym, 12, '')).toEqual([other]);
  });

  it('os do exercício atual vêm antes, o resto na ordem da API', () => {
    const a = pair({ id: 2, equipment_id: 40, equipment_name: 'Barra', exercise_id: 13, exercise_name: 'Supino' });
    const b = pair({ id: 3, equipment_id: 41, equipment_name: 'Halter', exercise_id: 12 });
    const c = pair({ id: 4, equipment_id: 42, equipment_name: 'Smith', exercise_id: 14, exercise_name: 'Outro' });
    expect(buildCatalogSection([a, b, c], gym, 12, '').map((p) => p.id)).toEqual([3, 2, 4]);
  });

  it('busca por equipamento ou exercício, sem acento', () => {
    const a = pair({ id: 2, equipment_id: 40, equipment_name: 'Máquina', exercise_id: 13, exercise_name: 'Supino' });
    const b = pair({ id: 3, equipment_id: 41, equipment_name: 'Halter', exercise_id: 14, exercise_name: 'Remada' });
    expect(buildCatalogSection([a, b], gym, 12, 'maquina').map((p) => p.id)).toEqual([2]);
    expect(buildCatalogSection([a, b], gym, 12, 'remad').map((p) => p.id)).toEqual([3]);
  });

  it('sem catálogo (vazio, nulo ou erro) devolve vazio', () => {
    expect(buildCatalogSection([], gym, 12, '')).toEqual([]);
    expect(buildCatalogSection(undefined, gym, 12, '')).toEqual([]);
  });

  it('lista da academia ainda não carregada (undefined/null) devolve vazio', () => {
    expect(buildCatalogSection([pair({})], undefined, 12, '')).toEqual([]);
    expect(buildCatalogSection([pair({})], null, 12, '')).toEqual([]);
  });

  it('lista da academia carregada e vazia: todos os pares do catálogo entram', () => {
    const a = pair({ id: 2, equipment_id: 8 });
    const b = pair({ id: 3, equipment_id: 9 });
    expect(buildCatalogSection([a, b], [], 12, '').map((p) => p.id)).toEqual([2, 3]);
  });
});

describe('catalogItemState', () => {
  it('com permissão e online: ação', () => {
    expect(catalogItemState(true, true)).toEqual({ kind: 'add', notice: null });
  });
  it('com permissão e offline: ação bloqueada com aviso de conexão', () => {
    expect(catalogItemState(true, false)).toEqual({ kind: 'add_offline', notice: CATALOG_NEEDS_ONLINE_NOTICE });
  });
  it('sem permissão: desabilitado, online ou não', () => {
    expect(catalogItemState(false, true)).toEqual({ kind: 'disabled', notice: CATALOG_NOT_IN_GYM_NOTICE });
    expect(catalogItemState(false, false).kind).toBe('disabled');
  });
});

describe('formatCatalogAddQuestion', () => {
  it('com nome da academia', () => {
    expect(formatCatalogAddQuestion('Crossover', 'Academia Central')).toBe(
      'Acrescentar Crossover à academia Academia Central? Ele ficará disponível para todos que treinam nela.',
    );
  });
  it('sem nome (nulo, indefinido ou vazio)', () => {
    const expected = 'Acrescentar Crossover a esta academia? Ele ficará disponível para todos que treinam nela.';
    expect(formatCatalogAddQuestion('Crossover', null)).toBe(expected);
    expect(formatCatalogAddQuestion('Crossover', undefined)).toBe(expected);
    expect(formatCatalogAddQuestion('Crossover', '  ')).toBe(expected);
  });
});

describe('findRecommendedItem', () => {
  const items = [gymItem({ id: 5, load_convention: 'per_side' }), gymItem({ id: 6 })];
  it('recomendado na lista: devolve o item com a convenção', () => {
    expect(findRecommendedItem(items, 5)?.load_convention).toBe('per_side');
  });
  it('recomendado fora da lista: undefined', () => {
    expect(findRecommendedItem(items, 99)).toBeUndefined();
    expect(findRecommendedItem([], 5)).toBeUndefined();
  });
});

describe('findAddedItem', () => {
  const target = pair({ equipment_id: 31, exercise_id: 12 });
  const fresh = [gymItem({ id: 77, equipment_id: 31, mapping_id: 12 }), gymItem({ id: 78, equipment_id: 31, mapping_id: 13, mapping_exercise_name: 'Outro' })];

  it('acha pelo links', () => {
    expect(findAddedItem([{ id: 77, mapping_id: 12, equipment_id: 31 }], fresh, target)?.id).toBe(77);
  });
  it('links vazio: cai na lista recarregada', () => {
    expect(findAddedItem([], fresh, target)?.id).toBe(77);
    expect(findAddedItem(null, fresh, target)?.id).toBe(77);
  });
  it('links com outro mapping_id é ignorado', () => {
    expect(findAddedItem([{ id: 78, mapping_id: 13, equipment_id: 31 }], fresh, target)?.id).toBe(77);
  });
  it('não encontra em lugar nenhum: null', () => {
    expect(findAddedItem([], [], target)).toBeNull();
  });
});
