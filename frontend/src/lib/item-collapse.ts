// Cards de item recolhidos no editor de Modelo (`/modelos-treino/[id]`).
//
// Só do navegador (`localStorage`, por Modelo), no mesmo esquema do colapso
// de slots (`slot-grid.ts`). Diferença: o slot é lembrado por POSIÇÃO, mas o
// item não pode ser — arrastar reordena os itens e a posição mudaria de card.
// Em memória a chave é o `localId` (o mesmo id do `SortableItem`, que
// sobrevive à reordenação); no `localStorage` vai o `id` do item no servidor,
// porque o `localId` é regerado a cada carregamento. O mapa `localId -> id`
// vem da hidratação e é refeito a cada save (versão nova = ids novos).
// Item ainda não salvo (sem `id`) fica recolhido só na sessão.

type HasItems = { items: ReadonlyArray<{ localId: string }> };

/** localIds recolhidos -> ids do servidor (crescentes, sem repetição) para gravar. */
export function serializeCollapsedItems(
  slots: ReadonlyArray<HasItems>,
  collapsed: ReadonlySet<string>,
  serverIds: ReadonlyMap<string, number>,
): number[] {
  const ids = new Set<number>();
  for (const slot of slots) {
    for (const item of slot.items) {
      if (!collapsed.has(item.localId)) continue;
      const id = serverIds.get(item.localId);
      if (id != null) ids.add(id);
    }
  }
  return [...ids].sort((a, b) => a - b);
}

/** Ids gravados -> localIds dos itens atuais. Ids que não existem mais (ou lixo) são descartados. */
export function hydrateCollapsedItems(
  slots: ReadonlyArray<HasItems>,
  serverIds: ReadonlyMap<string, number>,
  storedIds: unknown,
): Set<string> {
  const result = new Set<string>();
  if (!Array.isArray(storedIds) || storedIds.length === 0) return result;
  const wanted = new Set(storedIds.filter((id): id is number => typeof id === 'number' && Number.isInteger(id)));
  for (const slot of slots) {
    for (const item of slot.items) {
      const id = serverIds.get(item.localId);
      if (id != null && wanted.has(id)) result.add(item.localId);
    }
  }
  return result;
}

/** Liga/desliga um card. */
export function toggleCollapsedItem(collapsed: ReadonlySet<string>, localId: string): Set<string> {
  const next = new Set(collapsed);
  if (next.has(localId)) next.delete(localId);
  else next.add(localId);
  return next;
}

/** Recolhe (ou expande) de uma vez os cards indicados — o botão do slot. */
export function setItemsCollapsed(
  collapsed: ReadonlySet<string>,
  localIds: ReadonlyArray<string>,
  value: boolean,
): Set<string> {
  const next = new Set(collapsed);
  for (const id of localIds) {
    if (value) next.add(id);
    else next.delete(id);
  }
  return next;
}

/** Todos os cards da lista estão recolhidos? (lista vazia = não) */
export function allItemsCollapsed(collapsed: ReadonlySet<string>, localIds: ReadonlyArray<string>): boolean {
  return localIds.length > 0 && localIds.every((id) => collapsed.has(id));
}

export const ITEM_COLLAPSE_STORAGE_PREFIX = 'musclefit.templateItemCollapse.v1:';

export function itemCollapseStorageKey(templateId: number | string): string {
  return `${ITEM_COLLAPSE_STORAGE_PREFIX}${templateId}`;
}

/** JSON gravado -> ids; qualquer coisa fora do formato vira lista vazia. */
export function parseCollapsedItemIds(serialized: string | null | undefined): number[] {
  if (!serialized) return [];
  let parsed: unknown;
  try {
    parsed = JSON.parse(serialized);
  } catch {
    return [];
  }
  if (!Array.isArray(parsed)) return [];
  return parsed.filter((id): id is number => typeof id === 'number' && Number.isInteger(id) && id > 0);
}

type StorageLike = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;

function defaultStorage(): StorageLike | null {
  try {
    return typeof window !== 'undefined' ? window.localStorage : null;
  } catch {
    return null;
  }
}

export function readCollapsedItemIds(
  templateId: number | string,
  storage: StorageLike | null = defaultStorage(),
): number[] {
  if (!storage) return [];
  try {
    return parseCollapsedItemIds(storage.getItem(itemCollapseStorageKey(templateId)));
  } catch {
    return [];
  }
}

/** Grava os ids; lista vazia remove a chave. Falha é silenciosa. */
export function writeCollapsedItemIds(
  templateId: number | string,
  ids: number[],
  storage: StorageLike | null = defaultStorage(),
): void {
  if (!storage) return;
  try {
    const key = itemCollapseStorageKey(templateId);
    if (ids.length === 0) storage.removeItem(key);
    else storage.setItem(key, JSON.stringify(ids));
  } catch {
    // Cota estourada ou storage bloqueado: só não persiste.
  }
}
