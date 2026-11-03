import type { ComponentType } from 'react';
import { lazyScreen } from './lazy';

/**
 * Telas mobile da frente C (Treino, corpo, nutrição e conta). Chave = rota sem
 * barra final (segmentos dinâmicos como `[id]`); valor = `lazyScreen(...)`.
 * Compõe-se em `registry.ts`. Dono: frente C (`src/mobile/screens/telas/**`).
 */
export const SCREENS_C: Readonly<Record<string, ComponentType>> = {
  '/registro-exercicios': lazyScreen(() =>
    import('./telas/HistoricoScreen').then((m) => ({ default: m.HistoricoScreen })),
  ),
  '/registro-exercicios/[activityId]': lazyScreen(() =>
    import('./telas/TreinoDetalheScreen').then((m) => ({ default: m.TreinoDetalheScreen })),
  ),
  '/planos-treino': lazyScreen(() => import('./telas/PlanosScreen').then((m) => ({ default: m.PlanosScreen }))),
  '/planos-treino/[id]': lazyScreen(() =>
    import('./telas/PlanoDetalheScreen').then((m) => ({ default: m.PlanoDetalheScreen })),
  ),
  '/exercicios': lazyScreen(() => import('./telas/ExerciciosScreen').then((m) => ({ default: m.ExerciciosScreen }))),
  '/usuarios': lazyScreen(() => import('./telas/UsuariosScreen').then((m) => ({ default: m.UsuariosScreen }))),
  '/nutricao': lazyScreen(() => import('./telas/NutricaoDiarioScreen').then((m) => ({ default: m.NutricaoDiarioScreen }))),
  '/nutricao/favoritos': lazyScreen(() =>
    import('./telas/NutricaoFavoritosScreen').then((m) => ({ default: m.NutricaoFavoritosScreen })),
  ),
};
