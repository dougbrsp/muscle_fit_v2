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
  '/dashboard/nutricao': lazyScreen(() => import('./evolucao/NutricaoScreen').then((m) => ({ default: m.NutricaoScreen }))),
  '/dashboard/ciclo': lazyScreen(() => import('./evolucao/CicloScreen').then((m) => ({ default: m.CicloScreen }))),
  '/dashboard/checkin': lazyScreen(() => import('./evolucao/CheckinScreen').then((m) => ({ default: m.CheckinScreen }))),
  '/dashboard/contexto': lazyScreen(() => import('./evolucao/ContextoScreen').then((m) => ({ default: m.ContextoScreen }))),
  '/dashboard/engine': lazyScreen(() => import('./evolucao/EngineScreen').then((m) => ({ default: m.EngineScreen }))),
  '/dashboard/data-quality': lazyScreen(() => import('./evolucao/DataQualityScreen').then((m) => ({ default: m.DataQualityScreen }))),
  '/dashboard/insights': lazyScreen(() => import('./evolucao/InsightsScreens').then((m) => ({ default: m.InsightsDashboardScreen }))),
  '/insights': lazyScreen(() => import('./evolucao/InsightsScreens').then((m) => ({ default: m.InsightsIaScreen }))),
  '/dashboard/relatorios': lazyScreen(() => import('./evolucao/RelatoriosScreen').then((m) => ({ default: m.RelatoriosScreen }))),
  '/dashboard/exercicios/[exerciseId]': lazyScreen(() => import('./evolucao/ExercicioScreen').then((m) => ({ default: m.ExercicioScreen }))),
  '/agente-cardio': lazyScreen(() => import('./evolucao/AgenteCardioScreen').then((m) => ({ default: m.AgenteCardioScreen }))),
  '/intervals-icu': lazyScreen(() => import('./evolucao/IntervalsScreen').then((m) => ({ default: m.IntervalsScreen }))),
};
