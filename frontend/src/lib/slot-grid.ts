// Grade de slots do editor de Modelo de treino (`/modelos-treino/[id]`).
//
// A grade é só a FORMA de exibir a ordem do ciclo: lida linha por linha, a
// posição i (0-based) é o slot de `sequence` i+1 ("Dia N do ciclo"). Não há
// células vazias no meio — as vazias ficam só no fim. Arrastar um slot
// reordena o array de slots, nada mais.
//
// Espelha o backend (migração 062, `_validate_template_payload`): no máximo
// 8 slots e, com linhas e colunas preenchidas, `len(slots) <= rows * cols`.
//
// O estado "recolhido" de cada slot é só do navegador (`localStorage`, por
// Modelo), guardado como lista de POSIÇÕES — o `localId` do slot é regerado a
// cada carregamento, a posição não.

export const MAX_TEMPLATE_SLOTS = 8;
export const GRID_MAX_ROWS = 2;
export const GRID_MAX_COLS = 4;

export type SlotGrid = { rows: number; cols: number };

/** Grade padrão para `n` slots: até 4 colunas por linha, no máximo 2 linhas. */
export function defaultGrid(n: number): SlotGrid {
  const count = Math.max(Number.isFinite(n) ? Math.floor(n) : 1, 1);
  return {
    cols: Math.min(count, GRID_MAX_COLS),
    rows: Math.min(Math.ceil(count / GRID_MAX_COLS), GRID_MAX_ROWS),
  };
}

export function gridCapacity(g: SlotGrid): number {
  return g.rows * g.cols;
}

function isValidDimension(value: unknown, max: number): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 1 && value <= max;
}

export function isValidGrid(g: SlotGrid): boolean {
  return isValidDimension(g.rows, GRID_MAX_ROWS) && isValidDimension(g.cols, GRID_MAX_COLS);
}

/**
 * Grade gravada (GET do Modelo ou rascunho da periodização) -> grade do
 * builder. Qualquer valor ausente, fora da faixa ou que não comporte os `n`
 * slots cai na `defaultGrid(n)`.
 */
export function resolveGrid(rows: number | null | undefined, cols: number | null | undefined, n: number): SlotGrid {
  if (rows != null && cols != null) {
    const g = { rows, cols };
    if (isValidGrid(g) && gridCapacity(g) >= n) return g;
  }
  return defaultGrid(n);
}

/** A grade `g` pode ser aplicada com `nSlots` slots? (nunca apaga slot sozinho) */
export function canResizeGrid(g: SlotGrid, nSlots: number): boolean {
  return isValidGrid(g) && gridCapacity(g) >= nSlots;
}

/** Ainda cabe mais um slot: abaixo da capacidade da grade e do teto de 8. */
export function canAddSlot(g: SlotGrid, nSlots: number): boolean {
  return nSlots < gridCapacity(g) && nSlots < MAX_TEMPLATE_SLOTS;
}

/** Modelo legado com mais slots do que qualquer grade comporta (só proteção). */
export function isLegacyOverflow(nSlots: number): boolean {
  return nSlots > MAX_TEMPLATE_SLOTS;
}

/**
 * Grade enviada no save. Sempre os dois campos; só no legado com mais de 8
 * slots vão os dois nulos, porque nenhuma grade válida (máx. 2x4) o comporta
 * e o backend recusaria a capacidade.
 */
export function gridPayload(g: SlotGrid, nSlots: number): { grid_rows: number | null; grid_cols: number | null } {
  if (isLegacyOverflow(nSlots)) return { grid_rows: null, grid_cols: null };
  return { grid_rows: g.rows, grid_cols: g.cols };
}

/**
 * Move o slot da posição `fromIndex` para `toIndex`, deslocando os demais
 * (mesma semântica do `arrayMove` do dnd-kit). Índices fora da faixa devolvem
 * o próprio array.
 */
export function reorderSlots<T>(slots: T[], fromIndex: number, toIndex: number): T[] {
  if (
    fromIndex === toIndex ||
    fromIndex < 0 ||
    toIndex < 0 ||
    fromIndex >= slots.length ||
    toIndex >= slots.length
  ) {
    return slots;
  }
  const copy = slots.slice();
  const [moved] = copy.splice(fromIndex, 1);
  copy.splice(toIndex, 0, moved);
  return copy;
}

// --- Slots recolhidos (só no navegador) -------------------------------------

type HasLocalId = { localId: string };

/** localIds recolhidos -> posições (0-based, crescentes) para gravar. */
export function serializeCollapsed(slots: HasLocalId[], collapsed: ReadonlySet<string>): number[] {
  const positions: number[] = [];
  slots.forEach((slot, index) => {
    if (collapsed.has(slot.localId)) positions.push(index);
  });
  return positions;
}

/** Posições gravadas -> localIds dos slots atuais. Lixo e posições fora da faixa são ignorados. */
export function hydrateCollapsed(slots: HasLocalId[], positions: unknown): Set<string> {
  const result = new Set<string>();
  if (!Array.isArray(positions)) return result;
  for (const position of positions) {
    if (typeof position !== 'number' || !Number.isInteger(position)) continue;
    const slot = slots[position];
    if (slot) result.add(slot.localId);
  }
  return result;
}

export const SLOT_COLLAPSE_STORAGE_PREFIX = 'musclefit.templateSlotCollapse.v1:';

export function slotCollapseStorageKey(templateId: number | string): string {
  return `${SLOT_COLLAPSE_STORAGE_PREFIX}${templateId}`;
}

/** JSON gravado -> posições; qualquer coisa fora do formato vira lista vazia. */
export function parseCollapsedPositions(serialized: string | null | undefined): number[] {
  if (!serialized) return [];
  let parsed: unknown;
  try {
    parsed = JSON.parse(serialized);
  } catch {
    return [];
  }
  if (!Array.isArray(parsed)) return [];
  return parsed.filter((p): p is number => typeof p === 'number' && Number.isInteger(p) && p >= 0);
}

type StorageLike = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;

function defaultStorage(): StorageLike | null {
  try {
    return typeof window !== 'undefined' ? window.localStorage : null;
  } catch {
    return null;
  }
}

export function readCollapsedPositions(
  templateId: number | string,
  storage: StorageLike | null = defaultStorage(),
): number[] {
  if (!storage) return [];
  try {
    return parseCollapsedPositions(storage.getItem(slotCollapseStorageKey(templateId)));
  } catch {
    return [];
  }
}

/** Grava as posições; lista vazia remove a chave. Falha é silenciosa. */
export function writeCollapsedPositions(
  templateId: number | string,
  positions: number[],
  storage: StorageLike | null = defaultStorage(),
): void {
  if (!storage) return;
  try {
    const key = slotCollapseStorageKey(templateId);
    if (positions.length === 0) storage.removeItem(key);
    else storage.setItem(key, JSON.stringify(positions));
  } catch {
    // Cota estourada ou storage bloqueado: só não persiste.
  }
}
