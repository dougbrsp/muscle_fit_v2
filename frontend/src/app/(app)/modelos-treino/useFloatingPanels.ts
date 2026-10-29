'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  DEFAULT_PANEL_STATE,
  readFloatingPanels,
  writeFloatingPanels,
  type FloatingPanelsState,
  type FloatingPanelState,
  type PanelPosition,
} from '@/lib/floating-panel-geometry';

/** Controles de UMA janela, entregues ao `FloatingPanel`. */
export type FloatingPanelControls = {
  state: FloatingPanelState;
  onMove: (position: PanelPosition) => void;
  onToggleMinimized: () => void;
  onClose: () => void;
};

/**
 * Fase 7 (Frente A) — estado das janelas flutuantes do builder (posição,
 * minimizada, fechada), persistido no `localStorage`
 * (`musclefit.floatingPanels.v1`). A leitura acontece num efeito — no
 * primeiro render (e no export estático) o estado é o padrão, sem acesso ao
 * `window`. A gravação só ocorre depois da leitura, para não sobrescrever o
 * que estava salvo com o estado padrão.
 */
export function useFloatingPanels() {
  const [panels, setPanels] = useState<FloatingPanelsState>({});
  // Estado (não ref) de propósito: no commit da leitura o efeito de gravação
  // ainda vê `hydrated = false` e não grava o `{}` inicial por cima.
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    setPanels(readFloatingPanels());
    setHydrated(true);
  }, []);

  useEffect(() => {
    if (hydrated) writeFloatingPanels(panels);
  }, [panels, hydrated]);

  const update = useCallback((id: string, patch: Partial<FloatingPanelState>) => {
    setPanels((prev) => ({ ...prev, [id]: { ...DEFAULT_PANEL_STATE, ...prev[id], ...patch } }));
  }, []);

  const stateOf = useCallback((id: string): FloatingPanelState => panels[id] ?? DEFAULT_PANEL_STATE, [panels]);

  const controls = useCallback(
    (id: string): FloatingPanelControls => {
      const state = stateOf(id);
      return {
        state,
        onMove: (position) => update(id, { position }),
        onToggleMinimized: () => update(id, { minimized: !state.minimized }),
        onClose: () => update(id, { closed: true }),
      };
    },
    [stateOf, update],
  );

  /** Reabre as janelas fechadas entre `ids` (as que a tela pode mostrar agora). */
  const reopen = useCallback((ids: readonly string[]) => {
    setPanels((prev) => {
      const next = { ...prev };
      for (const id of ids) {
        if (next[id]?.closed) next[id] = { ...next[id], closed: false, minimized: false };
      }
      return next;
    });
  }, []);

  const isClosed = useCallback((id: string) => stateOf(id).closed, [stateOf]);

  return { controls, reopen, isClosed };
}
