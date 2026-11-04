import { EM_DASH, fmtDecimal } from '@/lib/dashboard-format';
import type { Metric } from '@/lib/dashboard-v3-types';
import { isDesktopOnlyPath } from '@/lib/nav-items';

/** "1 sessão" / "3 sessões": plural correto em português (apresentação pura). */
export function plural(n: number | null | undefined, one: string, many: string): string {
  const v = n ?? 0;
  return `${v.toLocaleString('pt-BR')} ${v === 1 ? one : many}`;
}

/** Texto de uma `Metric` do backend: `null` vira traço, nunca zero. Só apresentação. */
export function metricText(metric: Metric | null | undefined, digits = 1): string {
  if (!metric || metric.value == null) return EM_DASH;
  return `${fmtDecimal(metric.value, digits)}${metric.unit ? ` ${metric.unit}` : ''}`;
}

/** Limitações (texto pronto do backend) das métricas exibidas, sem repetição. */
export function metricLimitations(metrics: ReadonlyArray<Metric | null | undefined>): string[] {
  const all = metrics.flatMap((m) => m?.limitations ?? []);
  return Array.from(new Set(all));
}

/** Link interno que pode aparecer no celular: seções só do computador saem (D1). */
export function mobileSafeHref(href: string | null | undefined): string | null {
  if (!href) return null;
  return isDesktopOnlyPath(href.split('?')[0]) ? null : href;
}
