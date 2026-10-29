import { describe, expect, it } from 'vitest';

import {
  formatExerciseCount,
  formatSeriesCount,
  resolvePrincipalGroupId,
  slotGroupBudgets,
  slotGroupSets,
} from './slot-group-sets';
import type { ItemState } from './workout-plan-template-form';

type Item = Pick<ItemState, 'item_kind' | 'muscle_group_id' | 'sets_count'>;

const item = (overrides: Partial<Item>): Item => ({
  item_kind: 'equipment',
  muscle_group_id: 1,
  sets_count: 3,
  ...overrides,
});

const names = new Map<number, string>([
  [1, 'Peitoral'],
  [2, 'Tríceps'],
  [3, 'Ombros'],
  [4, 'Bíceps'],
  [10, 'Costas'],
  [11, 'Dorsal alto'],
  [12, 'Dorsal baixo'],
  [13, 'Romboides'],
]);

// Costas (10) <- Dorsal alto (11) <- Romboides (13, neto); Dorsal baixo (12) -> Costas.
const parents = new Map<number, number | null>([
  [1, null],
  [2, null],
  [3, null],
  [4, null],
  [10, null],
  [11, 10],
  [12, 10],
  [13, 11],
]);

describe('formatSeriesCount / formatExerciseCount', () => {
  it('singular e plural', () => {
    expect(formatSeriesCount(1)).toBe('1 série');
    expect(formatSeriesCount(2)).toBe('2 séries');
    expect(formatSeriesCount(16)).toBe('16 séries');
    expect(formatExerciseCount(1)).toBe('1 exercício');
    expect(formatExerciseCount(4)).toBe('4 exercícios');
  });
});

describe('resolvePrincipalGroupId', () => {
  it('sobe a cadeia até a raiz (filho e neto)', () => {
    expect(resolvePrincipalGroupId(11, parents)).toBe(10);
    expect(resolvePrincipalGroupId(13, parents)).toBe(10);
    expect(resolvePrincipalGroupId(10, parents)).toBe(10);
  });

  it('sem mapa, grupo fora do mapa ou pai desconhecido: o próprio grupo', () => {
    expect(resolvePrincipalGroupId(11)).toBe(11);
    expect(resolvePrincipalGroupId(99, parents)).toBe(99);
    expect(resolvePrincipalGroupId(5, new Map([[5, 77]]))).toBe(5);
  });

  it('ciclo de cadastro não trava: para no último antes de repetir', () => {
    const cyclic = new Map<number, number | null>([
      [1, 2],
      [2, 3],
      [3, 1],
    ]);
    expect(resolvePrincipalGroupId(1, cyclic)).toBe(3);
    expect(resolvePrincipalGroupId(2, cyclic)).toBe(1);
    expect(resolvePrincipalGroupId(4, new Map([[4, 4]]))).toBe(4);
  });
});

describe('slotGroupSets', () => {
  it('agrupa nos dois tipos de item e conta exercícios, sem somar séries (vêm do backend)', () => {
    const rows = slotGroupSets(
      {
        items: [
          item({ muscle_group_id: 1, sets_count: 4 }),
          item({ muscle_group_id: 2, sets_count: 3 }),
          item({ item_kind: 'muscle_group', muscle_group_id: 1, sets_count: 12 }),
        ],
      },
      names,
      parents,
    );
    expect(rows).toEqual([
      { muscleGroupId: 1, name: 'Peitoral', exercises: 2, memberGroupIds: [1], label: '2 exercícios' },
      { muscleGroupId: 2, name: 'Tríceps', exercises: 1, memberGroupIds: [2], label: '1 exercício' },
    ]);
    expect(rows[0]).not.toHaveProperty('sets');
  });

  it('agrega filhos e netos no grupo principal e mostra só o nome dele', () => {
    const rows = slotGroupSets(
      {
        items: [
          item({ muscle_group_id: 11, sets_count: 4 }),
          item({ muscle_group_id: 12, sets_count: 3 }),
          item({ muscle_group_id: 13, sets_count: 3 }),
          item({ muscle_group_id: 10, sets_count: 4 }),
          item({ muscle_group_id: 4, sets_count: 6 }),
        ],
      },
      names,
      parents,
    );
    expect(rows.map((r) => [r.name, r.label])).toEqual([
      ['Bíceps', '1 exercício'],
      ['Costas', '4 exercícios'],
    ]);
    expect(rows[1].muscleGroupId).toBe(10);
    expect(rows[1].memberGroupIds).toEqual([11, 12, 13, 10]);
  });

  it('subgrupo sem o principal no slot ainda aparece com o nome do principal', () => {
    const rows = slotGroupSets({ items: [item({ muscle_group_id: 13, sets_count: 1 })] }, names, parents);
    expect(rows).toEqual([
      { muscleGroupId: 10, name: 'Costas', exercises: 1, memberGroupIds: [13], label: '1 exercício' },
    ]);
  });

  it('sem mapa de pais (lista carregando), cada grupo é o próprio principal', () => {
    const rows = slotGroupSets(
      { items: [item({ muscle_group_id: 11, sets_count: 2 }), item({ muscle_group_id: 12, sets_count: 2 })] },
      names,
    );
    expect(rows.map((r) => r.name)).toEqual(['Dorsal alto', 'Dorsal baixo']);
  });

  it('ciclo no cadastro não quebra a agregação', () => {
    const cyclic = new Map<number, number | null>([
      [1, 2],
      [2, 1],
    ]);
    const rows = slotGroupSets({ items: [item({ muscle_group_id: 1, sets_count: 2 })] }, names, cyclic);
    expect(rows).toHaveLength(1);
    expect(rows[0].exercises).toBe(1);
  });

  it('ignora cardio', () => {
    const rows = slotGroupSets(
      { items: [item({ item_kind: 'cardio', muscle_group_id: 1, sets_count: 5 }), item({ sets_count: 2 })] },
      names,
      parents,
    );
    expect(rows.map((r) => r.label)).toEqual(['1 exercício']);
  });

  it('ignora item sem grupo; item sem séries conta como exercício', () => {
    const rows = slotGroupSets(
      {
        items: [
          item({ muscle_group_id: null, sets_count: 4 }),
          item({ muscle_group_id: 2, sets_count: null }),
          item({ muscle_group_id: 3, sets_count: 0 }),
          item({ muscle_group_id: 4, sets_count: 1 }),
          item({ muscle_group_id: 4, sets_count: null }),
        ],
      },
      names,
      parents,
    );
    expect(rows.map((r) => [r.name, r.label])).toEqual([
      ['Bíceps', '2 exercícios'],
      ['Ombros', '1 exercício'],
      ['Tríceps', '1 exercício'],
    ]);
  });

  it('slot vazio ou só de cardio devolve lista vazia', () => {
    expect(slotGroupSets({ items: [] }, names, parents)).toEqual([]);
    expect(slotGroupSets({ items: [item({ item_kind: 'cardio', sets_count: null })] }, names, parents)).toEqual([]);
  });

  it('grupo fora do mapa de nomes vira "Grupo #id" (inclusive o principal)', () => {
    expect(slotGroupSets({ items: [item({ muscle_group_id: 99, sets_count: 2 })] }, names)[0].name).toBe('Grupo #99');
    const orphanParent = new Map<number, number | null>([
      [50, 60],
      [60, null],
    ]);
    const rows = slotGroupSets({ items: [item({ muscle_group_id: 50, sets_count: 2 })] }, names, orphanParent);
    expect(rows[0].name).toBe('Grupo #60');
  });
});

describe('slotGroupBudgets (modo por orçamento)', () => {
  type BudgetItem = Pick<ItemState, 'item_kind' | 'muscle_group_id' | 'priority_min_pct' | 'priority_max_pct'>;
  const group = (muscle_group_id: number | null, min: number | null = null, max: number | null = null): BudgetItem => ({
    item_kind: 'muscle_group',
    muscle_group_id,
    priority_min_pct: min,
    priority_max_pct: max,
  });
  const budget = (
    muscle_group_id: number,
    pct_min: number,
    pct_max: number,
    sets_min: number | null,
    sets_max: number | null,
    has_target = true,
  ) => ({ muscle_group_id, pct_min, pct_max, sets_min, sets_max, has_target });

  it('lista todos os grupos, mesmo sem séries, com faixa, séries projetadas e exercícios', () => {
    const slot = { items: [group(1, 20, 30), group(2, 40, 60)] };
    const rows = slotGroupBudgets(slot, names, [budget(1, 20, 30, 3.2, 4.4), budget(2, 40, 60, 4, 6)], parents);
    expect(rows.map((r) => [r.name, r.label])).toEqual([
      ['Tríceps', '40–60% · ≈4–6 séries/1 exercício'],
      ['Peitoral', '20–30% · ≈3–4 séries/1 exercício'],
    ]);
  });

  it('séries iguais nos dois lados viram um número só; faixa idem', () => {
    const rows = slotGroupBudgets({ items: [group(1, 25, 25)] }, names, [budget(1, 25, 25, 3, 3.4)]);
    expect(rows[0].label).toBe('25% · ≈3 séries/1 exercício');
    const one = slotGroupBudgets({ items: [group(1, 5, 5)] }, names, [budget(1, 5, 5, 1, 1)]);
    expect(one[0].label).toBe('5% · ≈1 série/1 exercício');
  });

  it('grupo sem meta no tipo de treino mostra "sem meta"', () => {
    const rows = slotGroupBudgets({ items: [group(3, 10, 20)] }, names, [budget(3, 10, 20, null, null, false)]);
    expect(rows[0].label).toBe('10–20% · sem meta/1 exercício');
    expect(rows[0].hasTarget).toBe(false);
  });

  it('usa a faixa efetiva do backend para item sem % (divisão automática)', () => {
    const rows = slotGroupBudgets({ items: [group(4)] }, names, [budget(4, 50, 50, 5, 5)]);
    expect(rows[0].label).toBe('50% · ≈5 séries/1 exercício');
  });

  it('itens do mesmo grupo somam faixas e séries', () => {
    const slot = { items: [group(1, 10, 20), group(1, 10, 10)] };
    const rows = slotGroupBudgets(slot, names, [budget(1, 10, 20, 2, 4), budget(1, 10, 10, 2, 2)]);
    expect(rows).toHaveLength(1);
    expect(rows[0].label).toBe('20–30% · ≈4–6 séries/2 exercícios');
  });

  it('subgrupos e netos somam faixas, séries e exercícios no grupo principal', () => {
    const slot = { items: [group(11, 10, 15), group(13, 10, 15), group(4, 30, 40)] };
    const rows = slotGroupBudgets(
      slot,
      names,
      [budget(11, 10, 15, 1.5, 2), budget(13, 10, 15, 1.5, 2), budget(4, 30, 40, 3, 4)],
      parents,
    );
    expect(rows.map((r) => [r.name, r.label])).toEqual([
      ['Bíceps', '30–40% · ≈3–4 séries/1 exercício'],
      ['Costas', '20–30% · ≈3–4 séries/2 exercícios'],
    ]);
    expect(rows[1].muscleGroupId).toBe(10);
    expect(rows[1].memberGroupIds).toEqual([11, 13]);
  });

  it('agregado com um subgrupo sem meta: séries só dos que têm, sem "sem meta"', () => {
    const slot = { items: [group(11, 10, 10), group(12, 10, 10)] };
    const rows = slotGroupBudgets(slot, names, [budget(11, 10, 10, 2, 2), budget(12, 10, 10, null, null, false)], parents);
    expect(rows[0].label).toBe('20% · ≈2 séries/2 exercícios');
    expect(rows[0].hasTarget).toBe(true);
  });

  it('sem item_budgets (preview carregando) mostra só a faixa configurada', () => {
    const rows = slotGroupBudgets({ items: [group(1, 20, 30), group(2)] }, names);
    expect(rows.map((r) => [r.name, r.label])).toEqual([
      ['Peitoral', '20–30%/1 exercício'],
      ['Tríceps', 'faixa automática/1 exercício'],
    ]);
  });

  it('sem item_budgets, subgrupos somam a faixa configurada no principal', () => {
    const rows = slotGroupBudgets({ items: [group(11, 10, 20), group(12, 5, 10)] }, names, undefined, parents);
    expect(rows.map((r) => [r.name, r.label])).toEqual([['Costas', '15–30%/2 exercícios']]);
  });

  it('ignora cardio, item sem grupo e itens de equipamento', () => {
    const rows = slotGroupBudgets(
      {
        items: [
          { item_kind: 'cardio', muscle_group_id: null, priority_min_pct: null, priority_max_pct: null },
          group(null, 10, 20),
          { item_kind: 'equipment', muscle_group_id: 1, priority_min_pct: null, priority_max_pct: null },
        ],
      },
      names,
      undefined,
      parents,
    );
    expect(rows).toEqual([]);
  });
});
