import { describe, expect, it } from 'vitest';
import {
  allItemsCollapsed,
  hydrateCollapsedItems,
  itemCollapseStorageKey,
  parseCollapsedItemIds,
  readCollapsedItemIds,
  serializeCollapsedItems,
  setItemsCollapsed,
  toggleCollapsedItem,
  writeCollapsedItemIds,
} from './item-collapse';
import { hydrateSlotsWithItemIds, mapSavedItemServerIds, slotsToPayload } from './workout-plan-template-form';
import type { WorkoutPlanTemplateSlot, WorkoutPlanTemplateSlotItem } from './workout-plan-templates-types';

function apiItem(id: number | undefined, itemOrder: number, kind: WorkoutPlanTemplateSlotItem['item_kind'] = 'equipment') {
  return { id, item_order: itemOrder, item_kind: kind, sets_count: 3 } as WorkoutPlanTemplateSlotItem;
}

function apiSlot(sequence: number, items: WorkoutPlanTemplateSlotItem[]): WorkoutPlanTemplateSlot {
  return { sequence, name: `Dia ${sequence}`, weekday: null, slot_type: 'normal', priority_min_pct: null, priority_max_pct: null, items };
}

function memoryStorage() {
  const data = new Map<string, string>();
  return {
    data,
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, v),
    removeItem: (k: string) => void data.delete(k),
  };
}

const SERVER = [apiSlot(2, [apiItem(21, 1)]), apiSlot(1, [apiItem(11, 1), apiItem(12, 2), apiItem(13, 3, 'cardio')])];

describe('hydrateSlotsWithItemIds', () => {
  it('mapeia localId -> id do servidor na mesma ordem de hydrateSlots', () => {
    const { slots, itemServerIds } = hydrateSlotsWithItemIds(SERVER);
    expect(slots[0].items.map((it) => itemServerIds.get(it.localId))).toEqual([11, 12, 13]);
    expect(slots[1].items.map((it) => itemServerIds.get(it.localId))).toEqual([21]);
  });

  it('item sem id (rascunho da periodização) fica fora do mapa', () => {
    const { slots, itemServerIds } = hydrateSlotsWithItemIds([apiSlot(1, [apiItem(undefined, 1)])]);
    expect(slots[0].items).toHaveLength(1);
    expect(itemServerIds.size).toBe(0);
  });
});

describe('serializar / hidratar cards recolhidos', () => {
  it('ida e volta sobrevive à recarga (localIds novos) e à reordenação', () => {
    const first = hydrateSlotsWithItemIds(SERVER);
    const [a, b, c] = first.slots[0].items;
    const collapsed = new Set([a.localId, c.localId]);
    // Reordena (arrastar): a posição muda, o id do servidor não.
    const reordered = [{ ...first.slots[0], items: [c, b, a] }, first.slots[1]];
    const stored = serializeCollapsedItems(reordered, collapsed, first.itemServerIds);
    expect(stored).toEqual([11, 13]);

    const reload = hydrateSlotsWithItemIds(SERVER);
    const restored = hydrateCollapsedItems(reload.slots, reload.itemServerIds, stored);
    const [a2, b2, c2] = reload.slots[0].items;
    expect(restored).toEqual(new Set([a2.localId, c2.localId]));
    expect(restored.has(b2.localId)).toBe(false);
  });

  it('descarta ids que não existem mais e lixo', () => {
    const { slots, itemServerIds } = hydrateSlotsWithItemIds(SERVER);
    const restored = hydrateCollapsedItems(slots, itemServerIds, [999, 'x', 1.5, null, 21]);
    expect([...restored]).toEqual([slots[1].items[0].localId]);
    expect(hydrateCollapsedItems(slots, itemServerIds, 'lixo').size).toBe(0);
  });

  it('item não salvo (sem id) não é gravado; removido sai da lista', () => {
    const { slots, itemServerIds } = hydrateSlotsWithItemIds(SERVER);
    const novo = { ...slots[0].items[0], localId: 'novo' };
    const withNew = [{ ...slots[0], items: [...slots[0].items.slice(1), novo] }, slots[1]];
    const collapsed = new Set([slots[0].items[0].localId, 'novo']);
    // O item 11 foi removido (só o "novo" ficou no lugar): nada a gravar.
    expect(serializeCollapsedItems(withNew, collapsed, itemServerIds)).toEqual([]);
  });

  it('após o save, os ids novos da resposta assumem pelo sequence/item_order enviado', () => {
    const { slots } = hydrateSlotsWithItemIds(SERVER);
    const sent = [{ ...slots[0], items: [slots[0].items[2], slots[0].items[0]] }];
    const payload = slotsToPayload(sent);
    const response = [apiSlot(1, payload[0].items.map((it, i) => apiItem(100 + i, it.item_order, it.item_kind)))];
    const map = mapSavedItemServerIds(sent, response);
    expect(map.get(slots[0].items[2].localId)).toBe(100);
    expect(map.get(slots[0].items[0].localId)).toBe(101);
    expect(map.has(slots[0].items[1].localId)).toBe(false);
  });
});

describe('toggle e "todos do slot"', () => {
  it('liga/desliga um e aplica a vários', () => {
    const one = toggleCollapsedItem(new Set(), 'a');
    expect(one).toEqual(new Set(['a']));
    expect(toggleCollapsedItem(one, 'a').size).toBe(0);
    const all = setItemsCollapsed(new Set(['z']), ['a', 'b'], true);
    expect(all).toEqual(new Set(['z', 'a', 'b']));
    expect(allItemsCollapsed(all, ['a', 'b'])).toBe(true);
    expect(setItemsCollapsed(all, ['a', 'b'], false)).toEqual(new Set(['z']));
    expect(allItemsCollapsed(new Set(), [])).toBe(false);
  });
});

describe('localStorage', () => {
  it('grava por Modelo, lê de volta e remove com lista vazia', () => {
    const storage = memoryStorage();
    writeCollapsedItemIds(7, [11, 13], storage);
    expect(storage.data.get(itemCollapseStorageKey(7))).toBe('[11,13]');
    expect(readCollapsedItemIds(7, storage)).toEqual([11, 13]);
    expect(readCollapsedItemIds(8, storage)).toEqual([]);
    writeCollapsedItemIds(7, [], storage);
    expect(storage.data.has(itemCollapseStorageKey(7))).toBe(false);
  });

  it('JSON inválido, formato errado e storage que lança viram lista vazia, sem erro', () => {
    expect(parseCollapsedItemIds('{')).toEqual([]);
    expect(parseCollapsedItemIds('{"a":1}')).toEqual([]);
    expect(parseCollapsedItemIds('[1,-2,0,"3",4.5,5]')).toEqual([1, 5]);
    const broken = {
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
    expect(readCollapsedItemIds(1, broken)).toEqual([]);
    expect(() => writeCollapsedItemIds(1, [1], broken)).not.toThrow();
    expect(readCollapsedItemIds(1, null)).toEqual([]);
  });
});
