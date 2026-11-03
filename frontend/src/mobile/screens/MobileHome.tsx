'use client';

import { Button, Collapse, Skeleton, Stack } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { IconChevronDown, IconChevronUp, IconPlayerPlay } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import Link from 'next/link';
import { Suspense } from 'react';
import { InsightCard } from '@/components/dashboard/InsightCard';
import { StatusBadge } from '@/components/dashboard/StatusBadge';
import { AllClearState } from '@/components/dashboard/states';
import { CheckinCard } from '@/components/nutrition/CheckinCard';
import { NutritionTodayCard } from '@/components/nutrition/NutritionTodayCard';
import { dashboardKeys, fetchOverview } from '@/lib/dashboard-api';
import { dashboardHref, useDashboardFilters, type DashboardFilters } from '@/lib/dashboard-filters';
import { fmtInt } from '@/lib/dashboard-format';
import { NO_ACTIONS_MESSAGE } from '@/lib/dashboard-overview';
import { isDesktopOnlyPath } from '@/lib/nav-items';
import { DashboardOverviewClient } from '@/app/(app)/dashboard/DashboardOverviewClient';
import { ErrorState, ListBlock, ListGroup, ListRow, ScreenHeader, StickyAction } from '../ui';
import { safeInternalHref } from './safe-href';
import styles from './mobile-home.module.css';

/**
 * Início mobile (A2b). Mesmos hooks e queries da visão geral do desktop
 * (`useDashboardFilters` + `fetchOverview`, mesma chave de cache): NENHUM
 * cálculo novo. Todo texto e número vem pronto do backend (`answer`,
 * `headline`, contagens de grupos, `next_slot`); aqui só muda a hierarquia
 * para o celular: treino de hoje com o botão de iniciar ao alcance do
 * polegar, recuperação, próxima ação e, recolhido, o painel completo.
 * Superfícies agrupadas (ListGroup) em vez de cartões com contorno.
 */
export function MobileHome() {
  return (
    <Suspense fallback={<HomeSkeleton />}>
      <MobileHomeContent />
    </Suspense>
  );
}

function drilldownHref(raw: string, filters: DashboardFilters): string | null {
  const safe = safeInternalHref(raw);
  if (!safe) return null;
  // Seção só do computador: sem atalho no celular (a rota abriria a tela D1).
  if (isDesktopOnlyPath(safe.split('?')[0])) return null;
  return safe.startsWith('/dashboard') ? dashboardHref(safe, filters) : safe;
}

function HomeSkeleton() {
  return (
    <Stack gap="md" aria-busy="true" aria-label="Carregando o início">
      <Skeleton height={40} width={140} radius="md" />
      <Skeleton height={130} radius="lg" />
      <Skeleton height={120} radius="lg" />
      <Skeleton height={150} radius="lg" />
    </Stack>
  );
}

function MobileHomeContent() {
  const { filters, windowQuery } = useDashboardFilters();
  const [detailsOpened, { toggle: toggleDetails }] = useDisclosure(false);

  const consulta = useQuery({
    queryKey: dashboardKeys.overview(windowQuery),
    queryFn: () => fetchOverview(windowQuery),
  });
  const payload = consulta.data;

  if (consulta.isLoading) return <HomeSkeleton />;
  if (consulta.isError || !payload) {
    return (
      <ErrorState
        title="Não foi possível carregar o início"
        description="Você continua conectado. Verifique a conexão e tente de novo."
        onRetry={() => void consulta.refetch()}
      />
    );
  }

  const { consistency, recovery, next_actions: nextActions } = payload.blocks;
  const slotName = consistency.next_slot
    ? (consistency.next_slot.name ?? `slot ${consistency.next_slot.sequence}`)
    : null;
  // `drilldown_href` vem da API: so caminho interno (ver safe-href.ts).
  const recoveryHref = drilldownHref(recovery.drilldown_href, filters);
  const actionsHref = drilldownHref(nextActions.drilldown_href, filters);

  return (
    <>
      <ScreenHeader title="Início" />

      <ListGroup title="Treino de hoje">
        <ListBlock>
          <p className={styles.slot}>
            {slotName ?? (consistency.template_name ? consistency.template_name : 'Modo Treino')}
          </p>
          <p className={styles.answer}>{consistency.answer}</p>
          <p className={styles.meta}>
            Ciclo: {fmtInt(consistency.cycle_slots_done)}/{fmtInt(consistency.cycle_slots_total)}
          </p>
        </ListBlock>
      </ListGroup>

      <ListGroup title="Recuperação">
        <ListBlock>
          <div className={styles.badges}>
            <StatusBadge domain="severity" code={recovery.level} size="sm" />
            <StatusBadge domain="dataQuality" code={recovery.uncertainty} size="sm" />
          </div>
          <p className={styles.headline}>{recovery.headline}</p>
          <p className={styles.meta}>
            Grupos prontos: {fmtInt(recovery.groups_ready)} de {fmtInt(recovery.groups_total)}
          </p>
        </ListBlock>
        {recoveryHref ? <ListRow title="Ver prontidão e sinais" href={recoveryHref} /> : null}
      </ListGroup>

      <ListGroup title="Próxima ação">
        <ListBlock>
          {nextActions.items.length === 0 ? (
            <AllClearState>{NO_ACTIONS_MESSAGE}</AllClearState>
          ) : (
            nextActions.items
              .slice(0, 1)
              .map((insight) => (
                <InsightCard key={insight.fingerprint} insight={insight} filters={filters} compact />
              ))
          )}
        </ListBlock>
        {actionsHref ? (
          <ListRow
            title={
              nextActions.total_triggered > 1
                ? `Ver todas as ${fmtInt(nextActions.total_triggered)} sugestões`
                : 'Ver lista completa de sugestões'
            }
            href={actionsHref}
          />
        ) : null}
      </ListGroup>

      {/* D12: macros do dia e atalho de registro; o check-in semanal (N7) entra pelo `checkinSlot`. */}
      <div className={styles.gap}>
        <NutritionTodayCard className={styles.card} checkinSlot={<CheckinCard />} />
      </div>

      <Button
        variant="subtle"
        color="gray"
        fullWidth
        onClick={toggleDetails}
        aria-expanded={detailsOpened}
        rightSection={detailsOpened ? <IconChevronUp size={16} /> : <IconChevronDown size={16} />}
      >
        {detailsOpened ? 'Ocultar visão geral completa' : 'Ver visão geral completa'}
      </Button>
      <Collapse in={detailsOpened} transitionDuration={0}>
        {/* so monta quando aberto: sem o custo de render dos 6 blocos no Início */}
        {detailsOpened ? <DashboardOverviewClient /> : null}
      </Collapse>

      {/* CTA ao alcance do polegar: fixo acima da tab bar (desce com o teclado). */}
      <StickyAction>
        <Button
          component={Link}
          href="/modo-treino"
          size="lg"
          color="brand"
          fullWidth
          leftSection={<IconPlayerPlay size={20} />}
          aria-label={slotName ? `Iniciar treino: ${slotName}` : 'Iniciar treino'}
          style={{ minHeight: 48 }}
        >
          Iniciar treino
        </Button>
      </StickyAction>
    </>
  );
}
