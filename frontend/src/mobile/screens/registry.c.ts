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
  '/nutricao/plano': lazyScreen(() => import('./telas/NutricaoPlanoScreen').then((m) => ({ default: m.NutricaoPlanoScreen }))),
  '/nutricao/checkin': lazyScreen(() =>
    import('./telas/NutricaoCheckinScreen').then((m) => ({ default: m.NutricaoCheckinScreen })),
  ),
  '/nutricao/alimentos': lazyScreen(() =>
    import('./telas/NutricaoAlimentosScreen').then((m) => ({ default: m.NutricaoAlimentosScreen })),
  ),
  '/indicadores-fisicos': lazyScreen(() =>
    import('./telas/IndicadoresScreen').then((m) => ({ default: m.IndicadoresScreen })),
  ),
  '/perfil': lazyScreen(() => import('./telas/PerfilScreen').then((m) => ({ default: m.PerfilScreen }))),
  '/preferencias-treino': lazyScreen(() =>
    import('./telas/PreferenciasScreen').then((m) => ({ default: m.PreferenciasScreen })),
  ),
  '/volume-grupamentos': lazyScreen(() => import('./telas/VolumeScreen').then((m) => ({ default: m.VolumeScreen }))),
};
