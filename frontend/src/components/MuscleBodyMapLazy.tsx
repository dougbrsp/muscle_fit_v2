'use client';

import { Loader } from '@mantine/core';
import dynamic from 'next/dynamic';

/**
 * COD-14: o mapa corporal (regiões da arte em `bodymap/regions.v1.ts`, ~21 kB
 * gzip) carregado sob demanda. Mesmo componente; só muda quando o código chega.
 *
 * O placeholder é o MESMO `Loader` que o `MuscleBodyMap` já mostra enquanto
 * busca `GET /analytics/muscle-group-body-map`, então a tela passa pelo mesmo
 * estado visual de antes. `preloadMuscleBodyMap()` na montagem da tela começa
 * o download antes de o mapa ser desenhado.
 *
 * Os testes renderizam `MuscleBodyMap` direto (SSR), por isso ele continua
 * exportado com import estático em `MuscleBodyMap.tsx`.
 */
export const MuscleBodyMapLazy = dynamic(() => import('./MuscleBodyMap').then((m) => m.MuscleBodyMap), {
  ssr: false,
  loading: () => <Loader size="sm" color="brand" />,
});

/** Começa a baixar o chunk do mapa corporal (uso: `useEffect(preloadMuscleBodyMap, [])`). */
export function preloadMuscleBodyMap(): void {
  void import('./MuscleBodyMap');
}
