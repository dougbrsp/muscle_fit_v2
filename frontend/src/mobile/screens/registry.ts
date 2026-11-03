import type { ComponentType } from 'react';
import { isEvolucaoHub } from '../layout/primary-nav';
import { lazyScreen } from './lazy';
import { matchScreenKey } from './match';
import { SCREENS_B } from './registry.b';
import { SCREENS_C } from './registry.c';

/**
 * Telas mobile PRÓPRIAS por rota. Rota ausente = o shell renderiza a página de
 * sempre (mesma URL, mesmas queries). Só o shell mobile consulta este mapa: o
 * desktop nunca passa por aqui. Tudo carrega por `lazyScreen` (chunk sob
 * demanda) e aceita rotas dinâmicas (`/x/[id]`). As frentes B e C registram em
 * `registry.b.ts` e `registry.c.ts`.
 */
const HOME = lazyScreen(() => import('./MobileHome').then((m) => ({ default: m.MobileHome })));
const EVOLUCAO_HUB = lazyScreen(() =>
  import('./EvolucaoHub').then((m) => ({ default: m.EvolucaoHub })),
);

const SCREENS: Readonly<Record<string, ComponentType>> = {
  '/dashboard': HOME,
  ...SCREENS_B,
  ...SCREENS_C,
};

const KEYS = Object.keys(SCREENS);

/** `search` com ou sem `?` (o hub Evolução vive em `/dashboard/?hub=evolucao`). */
export function resolveMobileScreen(
  pathname: string | null | undefined,
  search: string | null | undefined = '',
): ComponentType | null {
  if (isEvolucaoHub(pathname, search)) return EVOLUCAO_HUB;
  const key = matchScreenKey(KEYS, pathname);
  return key ? SCREENS[key] : null;
}
