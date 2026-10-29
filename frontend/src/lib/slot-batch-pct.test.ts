import { describe, expect, it } from 'vitest';

import {
  EMPTY_BATCH_PCT_DRAFT,
  applyBatchPctToItems,
  hasPendingBatchPct,
  pendingBatchPctFields,
  supportsBatchPct,
  type BatchPctDraft,
  type BatchPctTargetItem,
} from './slot-batch-pct';

type Item = BatchPctTargetItem & { localId: string };

function slotItems(): Item[] {
  return [
    { localId: 'a', item_kind: 'muscle_group', priority_min_pct: null, priority_max_pct: null },
    { localId: 'b', item_kind: 'muscle_group', priority_min_pct: 10, priority_max_pct: 20 },
    { localId: 'c', item_kind: 'cardio', priority_min_pct: null, priority_max_pct: null },
  ];
}

// Réplica mínima da máquina de estado do `SlotBatchPctShortcut`: o que é
// digitado vai para `draft`; só o clique em "Aplicar" escreve nos itens.
function makeShortcut(items: Item[]) {
  let draft: BatchPctDraft = EMPTY_BATCH_PCT_DRAFT;
  let applied: BatchPctDraft = EMPTY_BATCH_PCT_DRAFT;
  let current = items;
  return {
    type(patch: Partial<BatchPctDraft>) {
      draft = { ...draft, ...patch };
    },
    get pending() {
      return hasPendingBatchPct(draft, applied);
    },
    get items() {
      return current;
    },
    clickApply() {
      if (!hasPendingBatchPct(draft, applied)) return;
      current = applyBatchPctToItems(current, draft);
      applied = draft;
    },
  };
}

describe('supportsBatchPct', () => {
  it('libera o atalho (e o botão Aplicar) no modo por grupo muscular', () => {
    expect(supportsBatchPct('percent_budget')).toBe(true);
  });

  it('não mostra o atalho no modo equipment_series', () => {
    expect(supportsBatchPct('equipment_series')).toBe(false);
  });
});

describe('pendência', () => {
  it('rascunho vazio não é pendência', () => {
    expect(hasPendingBatchPct(EMPTY_BATCH_PCT_DRAFT, EMPTY_BATCH_PCT_DRAFT)).toBe(false);
  });

  it('valor digitado e não aplicado é pendência', () => {
    const draft: BatchPctDraft = { priority_min_pct: 15, priority_max_pct: '' };
    expect(pendingBatchPctFields(draft, EMPTY_BATCH_PCT_DRAFT)).toEqual(['priority_min_pct']);
    expect(hasPendingBatchPct(draft, EMPTY_BATCH_PCT_DRAFT)).toBe(true);
  });

  it('não há pendência quando o rascunho já foi aplicado', () => {
    const draft: BatchPctDraft = { priority_min_pct: 15, priority_max_pct: 30 };
    expect(hasPendingBatchPct(draft, draft)).toBe(false);
  });
});

describe('applyBatchPctToItems', () => {
  it('distribui o % a todos os itens de grupo muscular e ignora os demais', () => {
    const result = applyBatchPctToItems(slotItems(), { priority_min_pct: 15, priority_max_pct: 30 });
    expect(result.map((i) => [i.priority_min_pct, i.priority_max_pct])).toEqual([
      [15, 30],
      [15, 30],
      [null, null],
    ]);
  });

  it('só toca no campo digitado', () => {
    const result = applyBatchPctToItems(slotItems(), { priority_min_pct: '', priority_max_pct: 40 });
    expect(result.map((i) => [i.priority_min_pct, i.priority_max_pct])).toEqual([
      [null, 40],
      [10, 40],
      [null, null],
    ]);
  });

  it('rascunho vazio devolve os itens intactos (mesma referência)', () => {
    const items = slotItems();
    expect(applyBatchPctToItems(items, EMPTY_BATCH_PCT_DRAFT)).toBe(items);
  });
});

describe('fluxo do botão Aplicar', () => {
  it('digitar sem aplicar NÃO altera os exercícios do slot', () => {
    const shortcut = makeShortcut(slotItems());
    shortcut.type({ priority_min_pct: 15, priority_max_pct: 30 });
    expect(shortcut.pending).toBe(true);
    expect(shortcut.items).toEqual(slotItems());
  });

  it('sair da tela sem aplicar descarta o rascunho: itens continuam como estavam', () => {
    const shortcut = makeShortcut(slotItems());
    shortcut.type({ priority_min_pct: 99 });
    // Desmontar o componente = jogar fora `draft`/`applied`, sem callback.
    expect(shortcut.items).toEqual(slotItems());
  });

  it('aplicar distribui e zera a pendência', () => {
    const shortcut = makeShortcut(slotItems());
    shortcut.type({ priority_min_pct: 15, priority_max_pct: 30 });
    shortcut.clickApply();
    expect(shortcut.pending).toBe(false);
    expect(shortcut.items.map((i) => [i.priority_min_pct, i.priority_max_pct])).toEqual([
      [15, 30],
      [15, 30],
      [null, null],
    ]);
  });

  it('clicar sem pendência é no-op', () => {
    const items = slotItems();
    const shortcut = makeShortcut(items);
    shortcut.clickApply();
    expect(shortcut.items).toBe(items);
  });

  it('novo valor após aplicar volta a ficar pendente até o próximo clique', () => {
    const shortcut = makeShortcut(slotItems());
    shortcut.type({ priority_min_pct: 15 });
    shortcut.clickApply();
    shortcut.type({ priority_min_pct: 25 });
    expect(shortcut.pending).toBe(true);
    expect(shortcut.items[0].priority_min_pct).toBe(15);
    shortcut.clickApply();
    expect(shortcut.items[0].priority_min_pct).toBe(25);
  });
});
