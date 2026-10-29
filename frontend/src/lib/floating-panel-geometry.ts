/**
 * Fase 7 (Frente A) — geometria e persistência das janelas flutuantes do
 * builder de Modelo (`/modelos-treino/[id]`).
 *
 * Funções puras: nada aqui toca o DOM. O componente mede a janela e a
 * viewport e pede a posição corrigida; o estado (posição, minimizada,
 * fechada) é gravado no `localStorage` do navegador — só conveniência por
 * usuário, então toda leitura/gravação tolera falha (aba anônima, cota,
 * storage bloqueado) e devolve o padrão sem quebrar a tela.
 */

/** Altura do header fixo do `AppShellLayout` (`header={{ height: 64 }}`). */
export const APP_HEADER_HEIGHT = 64;
/** Folga mínima entre a janela e as bordas da área útil. */
export const FLOATING_PANEL_MARGIN = 8;
/** Distância da borda na posição inicial. */
export const FLOATING_PANEL_GAP = 16;

export const FLOATING_PANELS_STORAGE_KEY = 'musclefit.floatingPanels.v1';

export type PanelPosition = { x: number; y: number };
export type PanelSize = { width: number; height: number };
export type Viewport = { width: number; height: number };

export type FloatingPanelState = {
  /** `null` = nunca arrastada: usa a posição padrão. */
  position: PanelPosition | null;
  minimized: boolean;
  closed: boolean;
};

export type FloatingPanelsState = Record<string, FloatingPanelState>;

export const DEFAULT_PANEL_STATE: FloatingPanelState = { position: null, minimized: false, closed: false };

function clampAxis(value: number, min: number, max: number): number {
  // Janela maior que a área útil: gruda no início (título sempre visível).
  if (max < min) return min;
  return Math.min(Math.max(value, min), max);
}

/**
 * Mantém a janela inteira dentro da viewport e abaixo do header. Se a janela
 * não couber, prioriza o canto superior esquerdo — a barra de título (por
 * onde se arrasta) nunca sai da tela.
 */
export function clampPosition(position: PanelPosition, size: PanelSize, viewport: Viewport): PanelPosition {
  const minX = FLOATING_PANEL_MARGIN;
  const minY = APP_HEADER_HEIGHT + FLOATING_PANEL_MARGIN;
  const maxX = viewport.width - size.width - FLOATING_PANEL_MARGIN;
  const maxY = viewport.height - size.height - FLOATING_PANEL_MARGIN;
  return {
    x: Math.round(clampAxis(position.x, minX, maxX)),
    y: Math.round(clampAxis(position.y, minY, maxY)),
  };
}

/**
 * Posição inicial: encostada à direita, logo abaixo do header, uma ao lado
 * da outra (índice 0 mais à direita) para as duas não se sobreporem.
 */
export function defaultPosition(index: number, size: PanelSize, viewport: Viewport): PanelPosition {
  const x = viewport.width - (index + 1) * (size.width + FLOATING_PANEL_GAP);
  const y = APP_HEADER_HEIGHT + FLOATING_PANEL_GAP;
  return clampPosition({ x, y }, size, viewport);
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function sanitizePanelState(raw: unknown): FloatingPanelState | null {
  if (!raw || typeof raw !== 'object') return null;
  const candidate = raw as Record<string, unknown>;
  const rawPosition = candidate.position as Record<string, unknown> | null | undefined;
  const position =
    rawPosition && isFiniteNumber(rawPosition.x) && isFiniteNumber(rawPosition.y)
      ? { x: rawPosition.x, y: rawPosition.y }
      : null;
  return {
    position,
    minimized: candidate.minimized === true,
    closed: candidate.closed === true,
  };
}

/** Converte o JSON gravado; qualquer coisa fora do formato vira estado vazio. */
export function parseFloatingPanels(serialized: string | null | undefined): FloatingPanelsState {
  if (!serialized) return {};
  let parsed: unknown;
  try {
    parsed = JSON.parse(serialized);
  } catch {
    return {};
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
  const result: FloatingPanelsState = {};
  for (const [id, value] of Object.entries(parsed as Record<string, unknown>)) {
    const state = sanitizePanelState(value);
    if (state) result[id] = state;
  }
  return result;
}

type StorageLike = Pick<Storage, 'getItem' | 'setItem'>;

function defaultStorage(): StorageLike | null {
  try {
    return typeof window !== 'undefined' ? window.localStorage : null;
  } catch {
    return null;
  }
}

export function readFloatingPanels(storage: StorageLike | null = defaultStorage()): FloatingPanelsState {
  if (!storage) return {};
  try {
    return parseFloatingPanels(storage.getItem(FLOATING_PANELS_STORAGE_KEY));
  } catch {
    return {};
  }
}

/** Grava o estado; falha é silenciosa (a janela continua funcionando nesta visita). */
export function writeFloatingPanels(state: FloatingPanelsState, storage: StorageLike | null = defaultStorage()): void {
  if (!storage) return;
  try {
    storage.setItem(FLOATING_PANELS_STORAGE_KEY, JSON.stringify(state));
  } catch {
    // Cota estourada ou storage bloqueado: só não persiste.
  }
}
