import { describe, expect, it } from 'vitest';
import { needsServerInsightsPage, sliceInsightsPage } from '@/lib/dashboard-insights-page';
import type { InsightsResponse } from '@/lib/dashboard-overview-types';

function lista(n: number, total = n): InsightsResponse {
  return {
    items: Array.from({ length: n }, (_, i) => ({ fingerprint: `f${i}` })),
    total,
    limit: 50,
    offset: 0,
    has_more: n < total,
    suppressed_total: 2,
    include_suppressed: false,
    cooldown_available: true,
    rules_evaluated: 11,
    rules_disabled: [],
    data_quality: 'HIGH',
    limitations: [],
    metric_versions: {},
  } as unknown as InsightsResponse;
}

describe('paginação local de insights', () => {
  it('uma página só: devolve tudo e não há mais', () => {
    const page = sliceInsightsPage(lista(8), 0, 20);
    expect(page.items).toHaveLength(8);
    expect(page.has_more).toBe(false);
    expect(page.total).toBe(8);
    expect(page.suppressed_total).toBe(2);
    expect(page.limit).toBe(20);
    expect(page.offset).toBe(0);
  });

  it('corta as páginas na mesma conta do servidor', () => {
    const bulk = lista(45);
    const p1 = sliceInsightsPage(bulk, 0, 20);
    const p2 = sliceInsightsPage(bulk, 20, 20);
    const p3 = sliceInsightsPage(bulk, 40, 20);
    expect([p1.items.length, p2.items.length, p3.items.length]).toEqual([20, 20, 5]);
    expect([p1.has_more, p2.has_more, p3.has_more]).toEqual([true, true, false]);
    expect(p3.items[0]).toEqual({ fingerprint: 'f40' });
  });

  it('lista completa (sem has_more) nunca vai ao servidor', () => {
    expect(needsServerInsightsPage(lista(45), 40, 20)).toBe(false);
  });

  it('lista maior que o limite vai ao servidor só quando a página passa do que veio', () => {
    const bulk = lista(50, 70);
    expect(needsServerInsightsPage(bulk, 0, 20)).toBe(false);
    expect(needsServerInsightsPage(bulk, 20, 20)).toBe(false);
    expect(needsServerInsightsPage(bulk, 40, 20)).toBe(true);
  });
});
