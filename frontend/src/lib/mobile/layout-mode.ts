'use client';

import { useEffect, useState } from 'react';

/**
 * Breakpoint mobile/desktop (plano mobile, seção 3.1).
 *
 * Origem: o `sm` do Mantine é 48em (= 768 px, ver `theme.breakpoints` padrão
 * em @mantine/core), o mesmo corte que o `AppShell` usa para trocar navbar.
 * Smartphone = largura < 768 px. Usamos 767.98px (e não 767px) para não
 * deixar fresta com larguras fracionárias entre 767 e 768 (zoom, DPR).
 * Tablet (>= 768 px) recebe o layout desktop. Sem User-Agent.
 */
export const MOBILE_BREAKPOINT_PX = 768;
export const MOBILE_MEDIA_QUERY = `(max-width: ${MOBILE_BREAKPOINT_PX - 0.02}px)`;

export type LayoutMode = 'mobile' | 'desktop';

/** Pura: converte o resultado do matchMedia no modo de layout. */
export function layoutModeFromMatch(matches: boolean): LayoutMode {
  return matches ? 'mobile' : 'desktop';
}

/**
 * `null` até montar (o export estático não sabe a largura no primeiro paint;
 * devolver um modo antes disso causaria hydration mismatch). Depois, reage a
 * resize/rotação via `change` do MediaQueryList.
 */
export function useLayoutMode(): LayoutMode | null {
  const [mode, setMode] = useState<LayoutMode | null>(null);

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
      setMode('desktop');
      return undefined;
    }
    const mql = window.matchMedia(MOBILE_MEDIA_QUERY);
    const update = () => setMode(layoutModeFromMatch(mql.matches));
    update();
    if (typeof mql.addEventListener === 'function') {
      mql.addEventListener('change', update);
      return () => mql.removeEventListener('change', update);
    }
    // Safari < 14
    mql.addListener(update);
    return () => mql.removeListener(update);
  }, []);

  return mode;
}
