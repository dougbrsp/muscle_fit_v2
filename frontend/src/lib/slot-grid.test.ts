import { describe, expect, it } from 'vitest';

import {
  GRID_MAX_COLS,
  GRID_MAX_ROWS,
  MAX_TEMPLATE_SLOTS,
  canAddSlot,
  canResizeGrid,
  defaultGrid,
  gridCapacity,
  gridPayload,
  hydrateCollapsed,
  parseCollapsedPositions,
  readCollapsedPositions,
  reorderSlots,
  resolveGrid,
  serializeCollapsed,
  slotCollapseStorageKey,
  writeCollapsedPositions,
} from './slot-grid';

function memoryStorage(initial: Record<string, string> = {}) {
  const data = new Map(Object.entries(initial));
  return {
    data,
    getItem: (key: string) => (data.has(key) ? data.get(key)! : null),
    setItem: (key: string, value: string) => {
      data.set(key, value);
    },
    removeItem: (key: string) => {
      data.delete(key);
    },
  };
}

const slots = (n: number) => Array.from({ length: n }, (_, i) => ({ localId: `s${i}` }));

describe('constantes', () => {
  it('espelham o backend (migração 062)', () => {
    expect(MAX_TEMPLATE_SLOTS).toBe(8);
    expect(GRID_MAX_ROWS).toBe(2);
    expect(GRID_MAX_COLS).toBe(4);
  });
});

describe('defaultGrid / gridCapacity', () => {
  it('até 4 colunas por linha, no máximo 2 linhas', () => {
    expect(defaultGrid(0)).toEqual({ rows: 1, cols: 1 });
    expect(defaultGrid(1)).toEqual({ rows: 1, cols: 1 });
    expect(defaultGrid(3)).toEqual({ rows: 1, cols: 3 });
    expect(defaultGrid(4)).toEqual({ rows: 1, cols: 4 });
    expect(defaultGrid(5)).toEqual({ rows: 2, cols: 4 });
    expect(defaultGrid(8)).toEqual({ rows: 2, cols: 4 });
    expect(defaultGrid(12)).toEqual({ rows: 2, cols: 4 });
  });

  it('capacidade = linhas x colunas', () => {
    expect(gridCapacity({ rows: 2, cols: 3 })).toBe(6);
    expect(gridCapacity(defaultGrid(5))).toBe(8);
  });
});

describe('resolveGrid', () => {
  it('usa a grade gravada quando válida e suficiente', () => {
    expect(resolveGrid(2, 2, 3)).toEqual({ rows: 2, cols: 2 });
  });

  it('cai na padrão com NULL, fora da faixa ou sem capacidade', () => {
    expect(resolveGrid(null, null, 3)).toEqual({ rows: 1, cols: 3 });
    expect(resolveGrid(2, null, 3)).toEqual({ rows: 1, cols: 3 });
    expect(resolveGrid(3, 4, 3)).toEqual({ rows: 1, cols: 3 });
    expect(resolveGrid(1, 5, 3)).toEqual({ rows: 1, cols: 3 });
    expect(resolveGrid(1, 2, 3)).toEqual({ rows: 1, cols: 3 });
  });
});

describe('canResizeGrid / canAddSlot', () => {
  it('bloqueia reduzir abaixo da quantidade de slots', () => {
    expect(canResizeGrid({ rows: 1, cols: 2 }, 3)).toBe(false);
    expect(canResizeGrid({ rows: 1, cols: 3 }, 3)).toBe(true);
    expect(canResizeGrid({ rows: 2, cols: 4 }, 0)).toBe(true);
  });

  it('recusa grade fora da faixa', () => {
    expect(canResizeGrid({ rows: 3, cols: 1 }, 1)).toBe(false);
    expect(canResizeGrid({ rows: 1, cols: 5 }, 1)).toBe(false);
    expect(canResizeGrid({ rows: 0, cols: 1 }, 0)).toBe(false);
  });

  it('adicionar só enquanto há célula livre e abaixo do teto de 8', () => {
    expect(canAddSlot({ rows: 1, cols: 3 }, 2)).toBe(true);
    expect(canAddSlot({ rows: 1, cols: 3 }, 3)).toBe(false);
    expect(canAddSlot({ rows: 2, cols: 4 }, 7)).toBe(true);
    expect(canAddSlot({ rows: 2, cols: 4 }, 8)).toBe(false);
    expect(canAddSlot({ rows: 2, cols: 4 }, 10)).toBe(false);
  });
});

describe('gridPayload', () => {
  it('sempre manda os dois campos', () => {
    expect(gridPayload({ rows: 2, cols: 3 }, 5)).toEqual({ grid_rows: 2, grid_cols: 3 });
  });

  it('legado com mais de 8 slots vai sem grade (nenhuma grade o comporta)', () => {
    expect(gridPayload({ rows: 2, cols: 4 }, 9)).toEqual({ grid_rows: null, grid_cols: null });
  });
});

describe('reorderSlots', () => {
  it('move e desloca os demais (ordem de leitura da grade)', () => {
    expect(reorderSlots(['a', 'b', 'c', 'd'], 0, 2)).toEqual(['b', 'c', 'a', 'd']);
    expect(reorderSlots(['a', 'b', 'c', 'd'], 3, 0)).toEqual(['d', 'a', 'b', 'c']);
  });

  it('não muta a entrada', () => {
    const input = ['a', 'b', 'c'];
    reorderSlots(input, 0, 2);
    expect(input).toEqual(['a', 'b', 'c']);
  });

  it('índices iguais ou fora da faixa devolvem o mesmo array', () => {
    const input = ['a', 'b'];
    expect(reorderSlots(input, 1, 1)).toBe(input);
    expect(reorderSlots(input, -1, 0)).toBe(input);
    expect(reorderSlots(input, 0, 2)).toBe(input);
  });
});

describe('serializeCollapsed / hydrateCollapsed', () => {
  it('round-trip por posição', () => {
    const list = slots(4);
    const positions = serializeCollapsed(list, new Set(['s1', 's3']));
    expect(positions).toEqual([1, 3]);
    const fresh = slots(4).map((s) => ({ localId: `novo-${s.localId}` }));
    expect(hydrateCollapsed(fresh, positions)).toEqual(new Set(['novo-s1', 'novo-s3']));
  });

  it('a posição segue o slot depois de arrastar', () => {
    const list = slots(3);
    const collapsed = new Set(['s0']);
    const moved = reorderSlots(list, 0, 2);
    expect(serializeCollapsed(moved, collapsed)).toEqual([2]);
  });

  it('ignora localIds que não existem mais e posições inválidas', () => {
    expect(serializeCollapsed(slots(2), new Set(['removido']))).toEqual([]);
    expect(hydrateCollapsed(slots(2), [0, 5, -1, 1.5, 'x'])).toEqual(new Set(['s0']));
    expect(hydrateCollapsed(slots(2), 'lixo')).toEqual(new Set());
  });
});

describe('persistência no localStorage', () => {
  it('usa a chave versionada por Modelo', () => {
    expect(slotCollapseStorageKey(42)).toBe('musclefit.templateSlotCollapse.v1:42');
  });

  it('grava, lê e remove quando vazio', () => {
    const storage = memoryStorage();
    writeCollapsedPositions(7, [0, 2], storage);
    expect(storage.data.get('musclefit.templateSlotCollapse.v1:7')).toBe('[0,2]');
    expect(readCollapsedPositions(7, storage)).toEqual([0, 2]);
    writeCollapsedPositions(7, [], storage);
    expect(storage.data.has('musclefit.templateSlotCollapse.v1:7')).toBe(false);
  });

  it('JSON inválido ou fora do formato vira lista vazia', () => {
    expect(parseCollapsedPositions('{')).toEqual([]);
    expect(parseCollapsedPositions('{"a":1}')).toEqual([]);
    expect(parseCollapsedPositions('[1,"x",-2,3]')).toEqual([1, 3]);
    expect(parseCollapsedPositions(null)).toEqual([]);
  });

  it('storage ausente ou que lança não quebra', () => {
    expect(readCollapsedPositions(1, null)).toEqual([]);
    const throwing = {
      getItem: () => {
        throw new Error('bloqueado');
      },
      setItem: () => {
        throw new Error('cota');
      },
      removeItem: () => {
        throw new Error('bloqueado');
      },
    };
    expect(readCollapsedPositions(1, throwing)).toEqual([]);
    expect(() => writeCollapsedPositions(1, [0], throwing)).not.toThrow();
    expect(() => writeCollapsedPositions(1, [], throwing)).not.toThrow();
  });
});
