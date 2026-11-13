'use client';

import { useMemo } from 'react';
import { dashboardHref, useDashboardFilters } from '@/lib/dashboard-filters';
import { evolucaoGroupsFor } from '../layout/primary-nav';
import { ListGroup, ListRow, ScreenHeader } from '../ui';
import { useCurrentUser } from '../use-current-user';

/**
 * Hub "Evolução" (aba do iPhone): lista por objetivo que leva às telas de
 * análise (`/dashboard/*`, Insights, Cardio). Só navegação: nenhum dado, número
 * ou regra. Vive em `/dashboard/?hub=evolucao`, então não cria rota nova no
 * desktop. Itens condicionais (Intervals.icu) seguem o papel/configuração.
 */
export function EvolucaoHub() {
  const userQuery = useCurrentUser();
  const { filters } = useDashboardFilters();
  const groups = useMemo(
    () => evolucaoGroupsFor(userQuery.data),
    [userQuery.data],
  );

  return (
    <>
      <ScreenHeader title="Evolução" subtitle="Seus resultados, por objetivo" />
      {groups.map((group) => (
        <ListGroup key={group.title} title={group.title}>
          {group.items.map((item) => (
            <ListRow
              key={item.href}
              icon={item.icon}
              title={item.label}
              href={item.href.startsWith('/dashboard') ? dashboardHref(item.href, filters) : item.href}
            />
          ))}
        </ListGroup>
      ))}
    </>
  );
}
