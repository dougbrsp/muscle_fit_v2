'use client';

import { Badge } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { Suspense, useMemo } from 'react';
import { DataQualityBadge, SEVERITY_DESCRIPTIONS, SEVERITY_ORDER, SeverityBadge } from '@/components/dashboard/SeverityBadge';
import { dashboardKeys, fetchDataQuality } from '@/lib/dashboard-api';
import { useDashboardFilters } from '@/lib/dashboard-filters';
import { fmtDate, fmtInt } from '@/lib/dashboard-format';
import type { DataQualityItem, Severity } from '@/lib/dashboard-v3-types';
import { EmptyState, ListBlock, ListGroup, ListRow, ScreenHeader } from '../../ui';
import styles from './evolucao.module.css';
import { BlockSkeleton, mobileSafeHref, PeriodFilter, QueryGate } from './shared';

/** `detail` pode ser uma data ISO (exemplos de sessão): formata quando for. */
function maybeDate(detail: string): string {
  return /^\d{4}-\d{2}-\d{2}/.test(detail) ? `${fmtDate(detail)}${detail.slice(10)}` : detail;
}

export function DataQualityScreen() {
  return (
    <Suspense fallback={<BlockSkeleton rows={4} label="Carregando qualidade dos dados" />}>
      <DataQualityContent />
    </Suspense>
  );
}

function DataQualityContent() {
  const { filters, setFilters, windowQuery, rangeError } = useDashboardFilters();
  const query = useQuery({
    queryKey: dashboardKeys.dataQuality(windowQuery),
    queryFn: () => fetchDataQuality(windowQuery),
  });
  const porSeveridade = useMemo(() => {
    const grupos = new Map<Severity, DataQualityItem[]>();
    for (const item of query.data?.items ?? []) grupos.set(item.severity, [...(grupos.get(item.severity) ?? []), item]);
    return SEVERITY_ORDER.filter((s) => grupos.has(s)).map((s) => ({ severidade: s, itens: grupos.get(s) ?? [] }));
  }, [query.data]);

  return (
    <>
      <ScreenHeader title="Qualidade dos dados" subtitle="O que está faltando ou inconsistente no que foi registrado, e o que isso impede de concluir." />
      <PeriodFilter filters={filters} setFilters={setFilters} rangeError={rangeError} />
      <QueryGate query={query} title="qualidade dos dados" rows={4}>
        {(data) => (
          <>
            <div className={styles.badges}>
              <DataQualityBadge quality={data.overall} label="Confiança nas conclusões" />
            </div>
            <p className={styles.note} style={{ marginBottom: 12 }}>
              {fmtInt(data.checked_rules)} verificações · {fmtDate(data.window.date_from)} a {fmtDate(data.window.date_to)}
            </p>
            {data.items.length === 0 ? (
              <EmptyState title="Tudo certo" description="Nenhum problema de qualidade encontrado no período." />
            ) : null}
            {porSeveridade.map(({ severidade, itens }) => (
              <div key={severidade}>
                <div className={styles.badges} style={{ marginTop: 8 }}>
                  <SeverityBadge severity={severidade} />
                </div>
                <p className={styles.note} style={{ marginBottom: 8 }}>{SEVERITY_DESCRIPTIONS[severidade]}</p>
                {itens.map((item) => {
                  const fix = mobileSafeHref(item.fix_link);
                  return (
                    <ListGroup key={item.code}>
                      <ListBlock>
                        <div className={styles.badges}>
                          <Badge color="brand" variant="light" radius="sm">{fmtInt(item.count)}</Badge>
                          {item.scope === 'catalog' ? <Badge color="gray" variant="outline" radius="sm">Cadastro</Badge> : null}
                        </div>
                        <p className={styles.headline}>{item.label}</p>
                        <p className={styles.meta}>{item.description}</p>
                        {item.scope === 'catalog' ? <p className={styles.meta}>Item de cadastro: não depende do período.</p> : null}
                        {item.examples.map((ex, i) => (
                          <p key={`${item.code}-${ex.id ?? i}`} className={styles.meta}>
                            • {ex.label}
                            {ex.detail ? ` — ${maybeDate(ex.detail)}` : ''}
                          </p>
                        ))}
                        {item.count > item.examples.length && item.examples.length > 0 ? (
                          <p className={styles.meta}>Mostrando {item.examples.length} de {item.count}.</p>
                        ) : null}
                        {item.limitations.map((l) => (
                          <p key={l} className={styles.meta}>{l}</p>
                        ))}
                      </ListBlock>
                      {fix ? (
                        <ListRow title="Corrigir" href={fix} />
                      ) : (
                        <ListRow title="Corrigir" subtitle="Disponível no computador" />
                      )}
                    </ListGroup>
                  );
                })}
              </div>
            ))}
            {data.limitations.length > 0 ? (
              <ListGroup>
                <ListBlock>
                  {data.limitations.map((l) => (
                    <p key={l} className={styles.meta} style={{ marginTop: 0 }}>{l}</p>
                  ))}
                </ListBlock>
              </ListGroup>
            ) : null}
          </>
        )}
      </QueryGate>
    </>
  );
}
