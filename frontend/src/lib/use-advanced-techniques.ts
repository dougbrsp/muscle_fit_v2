'use client';

import { useQuery } from '@tanstack/react-query';

import {
  ADVANCED_TECHNIQUES_QUERY_KEY,
  getAdvancedTechniqueCatalog,
  loadAdvancedTechniques,
  type AdvancedTechniquesResult,
} from './advanced-techniques';
import { apiFetch } from './api';

/**
 * Catálogo de técnicas avançadas (`GET /advanced-techniques`), compartilhado
 * por todas as telas via react-query. Enquanto carrega — ou se a chamada
 * falhar — devolve o fallback local (`isFallback: true`), então quem usa
 * nunca precisa tratar loading/erro. Chamar o hook também garante o re-render
 * dos helpers síncronos de `advanced-techniques.ts` quando o catálogo chega.
 */
export function useAdvancedTechniques(): AdvancedTechniquesResult {
  const query = useQuery({
    queryKey: ADVANCED_TECHNIQUES_QUERY_KEY,
    queryFn: () => loadAdvancedTechniques(apiFetch),
    // Catálogo muda raramente; em falha (isFallback) tenta de novo no próximo mount.
    staleTime: (q) => (q.state.data?.isFallback ? 0 : 30 * 60 * 1000),
    retry: false,
  });
  return query.data ?? { items: getAdvancedTechniqueCatalog(), isFallback: true };
}
