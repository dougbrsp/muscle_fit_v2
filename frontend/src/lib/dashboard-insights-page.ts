import type { InsightsResponse } from '@/lib/dashboard-overview-types';

/**
 * Paginação LOCAL de `/dashboard/insights` (PERF-02).
 *
 * O servidor recalcula a visão geral inteira (dezenas de queries) a cada chamada
 * e devolve só uma fatia (a paginação é um `slice` depois da avaliação). Então a
 * tela pede UMA vez a lista no limite máximo e corta a página aqui: paginar não
 * custa nenhuma chamada. Só quando a lista passa do limite (a primeira resposta
 * vem com `has_more` e a página pedida cai além do que veio) a tela volta a
 * paginar no servidor.
 *
 * Sem regra de domínio: ordem, deduplicação, cooldown e `total` são do servidor;
 * aqui só se recorta o array que ele mandou e se recalcula `has_more` com a mesma
 * conta dele (`offset + len < total`).
 */

/** Espelho de `INSIGHTS_MAX_LIMIT` (`api/app/analytics_dashboard.py`): o máximo que o endpoint aceita. */
export const INSIGHTS_BULK_LIMIT = 50;

/** A página pedida não cabe no que a lista completa trouxe: tem de ir ao servidor. */
export function needsServerInsightsPage(bulk: InsightsResponse, offset: number, pageSize: number): boolean {
  return bulk.has_more && offset + pageSize > bulk.items.length;
}

/** A página `[offset, offset + pageSize)` da lista completa, no formato da resposta do endpoint. */
export function sliceInsightsPage(bulk: InsightsResponse, offset: number, pageSize: number): InsightsResponse {
  const items = bulk.items.slice(offset, offset + pageSize);
  return { ...bulk, items, limit: pageSize, offset, has_more: offset + items.length < bulk.total };
}
