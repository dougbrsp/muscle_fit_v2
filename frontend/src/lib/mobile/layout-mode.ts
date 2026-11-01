'use client';

import { useEffect, useState } from 'react';

/**
 * Breakpoint mobile/desktop (plano mobile, seção 3.1).
 *
 * Origem: o `sm` do Mantine é 48em (= 768 px, ver `theme.breakpoints` padrão
 * em @mantine/core), o mesmo corte que o `AppShell` usa para trocar navbar.
 * Smartphone = largura < 768 px. A query usa em (47.99em), a mesma unidade do
 * `smallerThan="sm"` do Mantine, para os dois cortes coincidirem mesmo com
 * fonte raiz alterada. Sem fresta com larguras fracionárias. Tablet
 * (>= 768 px) recebe o layout desktop. Sem User-Agent.
 */
export const MOBILE_BREAKPOINT_PX = 768;
export const MOBILE_MEDIA_QUERY = '(max-width: 47.99em)';

export type LayoutMode = 'mobile' | 'desktop';

/** Pura: converte o resultado do matchMedia no modo de layout. */
export function layoutModeFromMatch(matches: boolean): LayoutMode {
  return matches ? 'mobile' : 'desktop';
}

export type MediaQueryLike = Pick<MediaQueryList, 'matches'> &
  Partial<
    Pick<MediaQueryList, 'addEventListener' | 'removeEventListener' | 'addListener' | 'removeListener'>
  >;

/**
 * Assina o MediaQueryList: chama `onChange` já com o valor atual e a cada
 * `change`. Devolve o cancelamento. Fallback para addListener (Safari < 14).
 */
export function subscribeMediaQuery(
  mql: MediaQueryLike,
  onChange: (matches: boolean) => void,
): () => void {
  const update = () => onChange(mql.matches);
  update();
  if (typeof mql.addEventListener === 'function' && typeof mql.removeEventListener === 'function') {
    mql.addEventListener('change', update);
    return () => mql.removeEventListener?.('change', update);
  }
  mql.addListener?.(update);
  return () => mql.removeListener?.(update);
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
    return subscribeMediaQuery(mql, (matches) => setMode(layoutModeFromMatch(matches)));
  }, []);

  return mode;
}
