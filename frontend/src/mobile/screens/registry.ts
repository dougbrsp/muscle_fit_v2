import type { ComponentType } from 'react';
import { MobileHome } from './MobileHome';

/**
 * Telas mobile PRÓPRIAS por rota (A2b). Rota ausente = o shell renderiza a
 * página de sempre (mesma URL, mesmas queries). Só o shell mobile consulta
 * este mapa: o desktop nunca passa por aqui.
 */
const SCREENS: Record<string, ComponentType> = {
  '/dashboard': MobileHome,
};

function normalize(pathname: string | null): string {
  if (!pathname) return '';
  return pathname.length > 1 && pathname.endsWith('/') ? pathname.slice(0, -1) : pathname;
}

export function resolveMobileScreen(pathname: string | null): ComponentType | null {
  return SCREENS[normalize(pathname)] ?? null;
}
