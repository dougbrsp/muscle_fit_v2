'use client';

import dynamic from 'next/dynamic';
import type { ComponentType } from 'react';
import { LoadingState } from '../ui';

/**
 * Tela mobile carregada sob demanda: o chunk só baixa quando a rota abre no
 * shell mobile (o desktop nunca passa pelo registry, então o bundle dele não
 * cresce). Uso nos registries das frentes:
 *
 *   lazyScreen(() => import('./telas/ExerciciosScreen').then((m) => ({ default: m.ExerciciosScreen })))
 */
export function lazyScreen(load: () => Promise<{ default: ComponentType }>): ComponentType {
  return dynamic(load, { ssr: false, loading: () => <LoadingState label="Carregando…" /> });
}
