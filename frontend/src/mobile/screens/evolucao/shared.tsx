'use client';

import { Skeleton, Stack } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import type { ReactNode } from 'react';
import { PartialState } from '@/components/dashboard/states';
import {
  DASHBOARD_WINDOW_LABELS,
  DEFAULT_DASHBOARD_WINDOW,
  dashboardWindowSelectData,
  type DashboardFilters,
  type DashboardWindow,
} from '@/lib/dashboard-filters';
import { EM_DASH, fmtDecimal } from '@/lib/dashboard-format';
import type { DataQuality, Metric } from '@/lib/dashboard-v3-types';
import { isDesktopOnlyPath } from '@/lib/nav-items';
import {
  ErrorState,
  FilterButton,
  FilterSheet,
  OfflineState,
  SegmentedFilter,
  useOnlineStatus,
} from '../../ui';
import styles from './evolucao.module.css';

/** Esqueleto genérico de bloco (altura reservada, sem salto de layout). */
export function BlockSkeleton({ rows = 3, label = 'Carregando' }: { rows?: number; label?: string }) {
  return (
    <Stack gap="sm" aria-busy="true" role="status" aria-label={label}>
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} height={64} radius="lg" />
      ))}
    </Stack>
  );
}

/** O que o porteiro precisa de uma consulta (aceita `UseQueryResult` ou uma composição). */
export type GateQuery<T> = {
  data: T | undefined;
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  refetch: () => unknown;
};

/**
 * Porteiro de consulta: carregando (esqueleto), offline, erro (com tentar de
 * novo) ou dados. O vazio é decidido por cada tela (`children` recebe os dados).
 */
export function QueryGate<T>({
  query,
  title,
  rows = 3,
  children,
}: {
  query: GateQuery<T>;
  title: string;
  rows?: number;
  children: (data: T) => ReactNode;
}) {
  const online = useOnlineStatus();
  if (query.isLoading) return <BlockSkeleton rows={rows} label={`Carregando: ${title}`} />;
  if (query.isError || query.data === undefined) {
    if (!online) return <OfflineState onRetry={() => void query.refetch()} />;
    const detail = query.error instanceof Error && query.error.message ? query.error.message : undefined;
    return (
      <ErrorState
        title={`Não foi possível carregar: ${title}`}
        description={detail ?? 'Verifique a conexão e tente de novo.'}
        onRetry={() => void query.refetch()}
      />
    );
  }
  return <>{children(query.data)}</>;
}

/** Texto de uma `Metric` do backend: `null` vira traço, nunca zero. */
export function metricText(metric: Metric | null | undefined, digits = 1): string {
  if (!metric || metric.value == null) return EM_DASH;
  return `${fmtDecimal(metric.value, digits)}${metric.unit ? ` ${metric.unit}` : ''}`;
}

/** Limitações (texto pronto do backend) das métricas exibidas, sem repetição. */
export function metricLimitations(metrics: ReadonlyArray<Metric | null | undefined>): string[] {
  const all = metrics.flatMap((m) => m?.limitations ?? []);
  return Array.from(new Set(all));
}

/** Rodapé com o que os números NÃO permitem concluir (antes só em Tooltip). */
export function LimitationsNote({ items }: { items: readonly string[] }) {
  if (items.length === 0) return null;
  return (
    <div className={styles.note}>
      <ul className={styles.noteList}>
        {items.map((t) => (
          <li key={t}>{t}</li>
        ))}
      </ul>
    </div>
  );
}

/** Link interno que pode aparecer no celular (seções só do computador saem). */
export function mobileSafeHref(href: string | null | undefined): string | null {
  if (!href) return null;
  return isDesktopOnlyPath(href.split('?')[0]) ? null : href;
}

/** `PartialState` do desktop (texto do backend), sem atalho para rota só-desktop. */
export function PartialNote({
  quality,
  limitations,
  title,
  fixHref,
  fixLabel,
}: {
  quality?: DataQuality | null;
  limitations?: string[];
  title?: string;
  fixHref?: string | null;
  fixLabel?: string;
}) {
  const href = fixHref === undefined ? '/dashboard/data-quality' : mobileSafeHref(fixHref);
  return (
    <div className={styles.gap}>
      <PartialState
        quality={quality}
        limitations={limitations}
        title={title}
        fixHref={href}
        fixLabel={fixLabel}
      />
    </div>
  );
}

const QUICK: readonly DashboardWindow[] = ['cycle', '7d', '28d', '90d'];
const SHORT_LABEL: Record<DashboardWindow, string> = {
  cycle: 'Ciclo',
  '7d': '7 dias',
  '28d': '28 dias',
  '90d': '90 dias',
  '180d': '180 dias',
  custom: 'Personalizado',
};

/**
 * Período do hub: segmentado com as janelas rápidas + folha "Filtros" com todas
 * as janelas e, na personalizada, as datas. Mesma fonte do desktop
 * (`useDashboardFilters`): a URL guarda `window`, `date_from`, `date_to`.
 */
export function PeriodFilter({
  filters,
  setFilters,
  rangeError,
  allowed,
  onChange,
}: {
  filters: DashboardFilters;
  setFilters: (patch: Partial<DashboardFilters>) => void;
  rangeError?: string | null;
  allowed?: readonly DashboardWindow[];
  /** Chamado ao trocar o período (ex.: zerar a paginação). */
  onChange?: () => void;
}) {
  const [opened, { open, close }] = useDisclosure(false);
  const all = allowed ?? (['7d', '28d', '90d', '180d', 'custom', 'cycle'] as const);
  const quick = QUICK.filter((w) => all.includes(w)).slice(0, 3);
  const current = all.includes(filters.window) ? filters.window : all[0];
  const options = quick.includes(current) ? quick : [...quick, current];
  const apply = (patch: Partial<DashboardFilters>) => {
    onChange?.();
    setFilters(patch);
  };
  const active = current !== DEFAULT_DASHBOARD_WINDOW ? 1 : 0;

  return (
    <>
      <div className={styles.toolbar}>
        <div className={styles.toolbarGrow}>
          <SegmentedFilter
            label="Período"
            value={current}
            onChange={(w) => apply({ window: w })}
            options={options.map((w) => ({ value: w, label: SHORT_LABEL[w] }))}
          />
        </div>
        <FilterButton activeCount={active} onClick={open} />
      </div>
      {rangeError ? (
        <p className={styles.err} role="alert">
          {rangeError} Enquanto isso, a consulta usa o período padrão.
        </p>
      ) : null}
      <FilterSheet
        opened={opened}
        onClose={close}
        activeCount={active}
        onClear={() => apply({ window: DEFAULT_DASHBOARD_WINDOW, dateFrom: null, dateTo: null })}
      >
        <div className={styles.field}>
          <label className={styles.fieldLabel} htmlFor="mob-periodo">
            Período
          </label>
          <select
            id="mob-periodo"
            className={styles.dateInput}
            value={current}
            onChange={(e) => apply({ window: e.currentTarget.value as DashboardWindow })}
          >
            {dashboardWindowSelectData(all).map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>
        {current === 'custom' ? (
          <>
            <div className={styles.field}>
              <label className={styles.fieldLabel} htmlFor="mob-de">
                De
              </label>
              <input
                id="mob-de"
                type="date"
                className={styles.dateInput}
                value={filters.dateFrom ?? ''}
                onChange={(e) => apply({ dateFrom: e.currentTarget.value || null })}
              />
            </div>
            <div className={styles.field}>
              <label className={styles.fieldLabel} htmlFor="mob-ate">
                Até
              </label>
              <input
                id="mob-ate"
                type="date"
                className={styles.dateInput}
                value={filters.dateTo ?? ''}
                onChange={(e) => apply({ dateTo: e.currentTarget.value || null })}
              />
            </div>
            {rangeError ? <p className={styles.err}>{rangeError}</p> : null}
          </>
        ) : null}
      </FilterSheet>
    </>
  );
}

/** Rótulo do período atual (para subtítulo). */
export function windowLabel(filters: DashboardFilters): string {
  return DASHBOARD_WINDOW_LABELS[filters.window];
}
