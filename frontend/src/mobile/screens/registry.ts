import type { ComponentType } from 'react';
import { isEvolucaoHub } from '../layout/primary-nav';
import { EvolucaoHub } from './EvolucaoHub';
import { MobileHome } from './MobileHome';
import { matchScreenKey } from './match';
import { SCREENS_B } from './registry.b';
import { SCREENS_C } from './registry.c';

/**
 * Telas mobile PRÓPRIAS por rota. Rota ausente = o shell renderiza a página de
 * sempre (mesma URL, mesmas queries). Só o shell mobile consulta este mapa: o
 * desktop nunca passa por aqui. Início e hub Evolução são estáticos (a PWA abre
 * offline sem chunk fora do precache); as telas de B e C carregam por
 * `lazyScreen` (chunk sob demanda). Aceita rotas dinâmicas (`/x/[id]`). As frentes B e C registram em
 * `registry.b.ts` e `registry.c.ts`.
 */
const SCREENS: Readonly<Record<string, ComponentType>> = {
  '/dashboard': MobileHome,
  ...SCREENS_B,
  ...SCREENS_C,
};

const KEYS = Object.keys(SCREENS);

/** `search` com ou sem `?` (o hub Evolução vive em `/dashboard/?hub=evolucao`). */
export function resolveMobileScreen(
  pathname: string | null | undefined,
  search: string | null | undefined = '',
): ComponentType | null {
  if (isEvolucaoHub(pathname, search)) return EvolucaoHub;
  const key = matchScreenKey(KEYS, pathname);
  return key ? SCREENS[key] : null;
}
