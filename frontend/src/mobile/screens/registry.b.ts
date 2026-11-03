import type { ComponentType } from 'react';
import { lazyScreen } from './lazy';

/**
 * Telas mobile da frente B (Evolução e dashboards). Chave = rota sem barra
 * final (segmentos dinâmicos como `[id]`); valor = `lazyScreen(...)`. Compõe-se
 * em `registry.ts`. Dono: frente B (`src/mobile/screens/evolucao/**`).
 */
export const SCREENS_B: Readonly<Record<string, ComponentType>> = {
  '/dashboard/treinos': lazyScreen(() => import('./evolucao/TreinosScreen').then((m) => ({ default: m.TreinosScreen }))),
  '/dashboard/progressao': lazyScreen(() => import('./evolucao/ProgressaoScreen').then((m) => ({ default: m.ProgressaoScreen }))),
  '/dashboard/recovery': lazyScreen(() => import('./evolucao/RecoveryScreen').then((m) => ({ default: m.RecoveryScreen }))),
  '/dashboard/musculos': lazyScreen(() => import('./evolucao/MusculosScreen').then((m) => ({ default: m.MusculosScreen }))),
  '/dashboard/corpo': lazyScreen(() => import('./evolucao/CorpoScreen').then((m) => ({ default: m.CorpoScreen }))),
  '/dashboard/cardio': lazyScreen(() => import('./evolucao/CardioScreen').then((m) => ({ default: m.CardioScreen }))),
};
