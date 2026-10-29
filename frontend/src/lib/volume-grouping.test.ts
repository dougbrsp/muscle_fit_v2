import { describe, expect, it } from 'vitest';
import {
  DEFAULT_VOLUME_GROUPING_LEVEL,
  VOLUME_GROUPING_LABELS,
  VOLUME_GROUPING_OPTIONS,
  VOLUME_GROUPING_STORAGE_KEY,
  compareVolumeGroups,
  groupingKeyOf,
  readVolumeGroupingLevel,
  sumByVolumeGroup,
  writeVolumeGroupingLevel,
  type GroupableVolumeRow,
  type VolumeGroupingLevel,
} from './volume-grouping';
import groupingParity from './volume-grouping.parity.json';

// Paridade com o dono do comparativo por slot (`slot_session_comparison.grouping_key_of`):
// o pytest `test_chave_de_agrupamento_paridade_com_o_frontend` lê a mesma fixture.
describe('paridade da chave com o backend (volume-grouping.parity.json)', () => {
  it.each(groupingParity.cases)('$level → $expected.key', ({ level, row: input, expected }) => {
    expect(groupingKeyOf(input as GroupableVolumeRow, level as VolumeGroupingLevel)).toEqual(expected);
  });
});

// Linhas como a API devolve (campos resolvidos pelo backend, cadastro do DEV):
// a tela nunca decide pai nem macro-grupo.
type Row = GroupableVolumeRow & { volume_kg: number };

const row = (
  muscle_group: string,
  parent: [number | null, string],
  volumeClass: [number | null, string, number | null],
  volume_kg: number,
): Row => ({
  muscle_group,
  parent_group_id: parent[0],
  parent_group_name: parent[1],
  volume_class_id: volumeClass[0],
  volume_class_name: volumeClass[1],
  volume_class_order: volumeClass[2],
  volume_kg,
});

const GRANDES: [number, string, number] = [1, 'Grandes', 0];
const PEQUENOS: [number, string, number] = [2, 'Pequenos', 1];
const OUTROS: [number, string, number] = [3, 'Outros', 2];
const SEM_MACRO: [null, string, null] = [null, 'Sem macro-grupo', null];

const ROWS: Row[] = [
  row('Costas', [6, 'Costas'], GRANDES, 1000),
  row('Costas meio', [6, 'Costas'], GRANDES, 400),
  row('Peitoral inferior', [12, 'Peitoral'], GRANDES, 300),
  row('Peitoral', [12, 'Peitoral'], GRANDES, 900),
  row('Bíceps', [5, 'Bíceps'], PEQUENOS, 200),
  row('Antebraço anterior', [50, 'Antebraço'], PEQUENOS, 50),
  row('Abdômen', [1, 'Abdômen'], OUTROS, 0),
  row('Core', [55, 'Core'], SEM_MACRO, 10),
  row('Outros', [null, 'Outros'], SEM_MACRO, 5),
  row('Costas', [6, 'Costas'], GRANDES, 600),
];

const LEVELS: VolumeGroupingLevel[] = ['volume_class', 'parent_group', 'muscle_group'];

const total = (items: Array<{ total: number }>) => items.reduce((acc, item) => acc + item.total, 0);

describe('sumByVolumeGroup', () => {
  it('a mesma entrada nos três níveis mantém o total geral', () => {
    const totals = LEVELS.map((level) => total(sumByVolumeGroup(ROWS, level, (r) => r.volume_kg)));
    expect(totals).toEqual([3465, 3465, 3465]);
  });

  it('macro-grupo: ordem do cadastro e o balde do backend por último', () => {
    const out = sumByVolumeGroup(ROWS, 'volume_class', (r) => r.volume_kg);
    expect(out.map(({ group, total: value }) => [group.label, value])).toEqual([
      ['Grandes', 3200],
      ['Pequenos', 250],
      ['Outros', 0],
      ['Sem macro-grupo', 15],
    ]);
  });

  it('grupo pai: a fatia soma no pai, em ordem alfabética', () => {
    const out = sumByVolumeGroup(ROWS, 'parent_group', (r) => r.volume_kg);
    expect(out.map(({ group, total: value }) => [group.label, value])).toEqual([
      ['Abdômen', 0],
      ['Antebraço', 50],
      ['Bíceps', 200],
      ['Core', 10],
      ['Costas', 2000],
      ['Outros', 5],
      ['Peitoral', 1200],
    ]);
  });

  it('grupo: o nome como hoje, em ordem alfabética', () => {
    const out = sumByVolumeGroup(ROWS, 'muscle_group', (r) => r.volume_kg);
    expect(out.map(({ group }) => group.label)).toEqual([
      'Abdômen',
      'Antebraço anterior',
      'Bíceps',
      'Core',
      'Costas',
      'Costas meio',
      'Outros',
      'Peitoral',
      'Peitoral inferior',
    ]);
    expect(out.find(({ group }) => group.label === 'Costas')?.total).toBe(1600);
  });
});

describe('groupingKeyOf', () => {
  it('a chave do pai e do macro-grupo é o id; o rótulo vem da API', () => {
    const fatia = ROWS[1];
    expect(groupingKeyOf(fatia, 'parent_group')).toEqual({ key: 'pai:6', label: 'Costas', order: null });
    expect(groupingKeyOf(fatia, 'volume_class')).toEqual({ key: 'macro:1', label: 'Grandes', order: 0 });
    expect(groupingKeyOf(fatia, 'muscle_group')).toEqual({ key: 'grupo:Costas meio', label: 'Costas meio', order: null });
  });

  it('sem id (grupo fora do cadastro) usa o rótulo do backend', () => {
    const outros = ROWS[8];
    expect(groupingKeyOf(outros, 'parent_group').key).toBe('pai:Outros');
    expect(groupingKeyOf(outros, 'volume_class')).toEqual({
      key: 'macro:sem-classe',
      label: 'Sem macro-grupo',
      order: null,
    });
  });

  it('API sem os campos (janela de deploy): a linha fica no próprio grupo', () => {
    const antiga = { muscle_group: 'Costas meio' };
    for (const level of LEVELS) {
      expect(groupingKeyOf(antiga, level)).toEqual({ key: 'grupo:Costas meio', label: 'Costas meio', order: null });
    }
  });
});

describe('compareVolumeGroups', () => {
  it('ordem do cadastro antes do nome; sem ordem vai para o fim', () => {
    const sorted = [
      { key: 'c', label: 'A', order: null },
      { key: 'b', label: 'Z', order: 1 },
      { key: 'a', label: 'B', order: 0 },
    ].sort(compareVolumeGroups);
    expect(sorted.map((group) => group.key)).toEqual(['a', 'b', 'c']);
  });
});

describe('seletor e preferência', () => {
  it('padrão é macro-grupo; rótulos curtos na ordem do seletor', () => {
    expect(DEFAULT_VOLUME_GROUPING_LEVEL).toBe('volume_class');
    expect(VOLUME_GROUPING_OPTIONS.map((option) => option.label)).toEqual(['Macro-grupo', 'Grupo pai', 'Grupo']);
    expect(VOLUME_GROUPING_LABELS.parent_group).toBe('Grupo pai');
  });

  it('lê e grava no storage; valor inválido, ausente ou erro caem no padrão', () => {
    const data = new Map<string, string>();
    const storage = {
      getItem: (key: string) => data.get(key) ?? null,
      setItem: (key: string, value: string) => void data.set(key, value),
    };
    expect(readVolumeGroupingLevel(storage)).toBe('volume_class');
    writeVolumeGroupingLevel(storage, 'parent_group');
    expect(data.get(VOLUME_GROUPING_STORAGE_KEY)).toBe('parent_group');
    expect(readVolumeGroupingLevel(storage)).toBe('parent_group');
    data.set(VOLUME_GROUPING_STORAGE_KEY, 'exercicio');
    expect(readVolumeGroupingLevel(storage)).toBe('volume_class');

    const quebrado = {
      getItem: () => {
        throw new Error('bloqueado');
      },
      setItem: () => {
        throw new Error('quota');
      },
    };
    expect(readVolumeGroupingLevel(quebrado)).toBe('volume_class');
    expect(() => writeVolumeGroupingLevel(quebrado, 'muscle_group')).not.toThrow();
    expect(readVolumeGroupingLevel(null)).toBe('volume_class');
    expect(() => writeVolumeGroupingLevel(undefined, 'muscle_group')).not.toThrow();
  });
});
