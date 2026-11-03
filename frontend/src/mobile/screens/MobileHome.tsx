'use client';

import { Button, Collapse, Group, Skeleton, Stack, Text, Title } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { IconChevronDown, IconChevronUp, IconPlayerPlay } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import Link from 'next/link';
import { Suspense, type ReactNode } from 'react';
import { InsightCard } from '@/components/dashboard/InsightCard';
import { StatusBadge } from '@/components/dashboard/StatusBadge';
import { AllClearState } from '@/components/dashboard/states';
import { NutritionTodayCard } from '@/components/nutrition/NutritionTodayCard';
import { dashboardKeys, fetchOverview } from '@/lib/dashboard-api';
import { dashboardHref, useDashboardFilters, type DashboardFilters } from '@/lib/dashboard-filters';
import { fmtInt } from '@/lib/dashboard-format';
import { NO_ACTIONS_MESSAGE } from '@/lib/dashboard-overview';
import { MOBILE_TOKENS } from '@/mobile/tokens';
import { DashboardOverviewClient } from '@/app/(app)/dashboard/DashboardOverviewClient';
import { ErrorState } from '../ui';
import { safeInternalHref } from './safe-href';
import styles from './mobile-home.module.css';

/**
 * Início mobile (A2b). Mesmos hooks e queries da visão geral do desktop
 * (`useDashboardFilters` + `fetchOverview`, mesma chave de cache): NENHUM
 * cálculo novo. Todo texto e número vem pronto do backend (`answer`,
 * `headline`, contagens de grupos, `next_slot`); aqui só muda a hierarquia
 * para o celular: treino de hoje com o botão de iniciar ao alcance do
 * polegar, recuperação, próxima ação e, recolhido, o painel completo.
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
  return safe.startsWith('/dashboard') ? dashboardHref(safe, filters) : safe;
}

function HomeSkeleton() {
  return (
    <Stack gap="md" aria-busy="true" aria-label="Carregando o início">
      <Skeleton height={140} radius="lg" />
      <Skeleton height={120} radius="lg" />
      <Skeleton height={160} radius="lg" />
    </Stack>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className={styles.card} aria-label={title}>
      <Text size="xs" fw={700} tt="uppercase" c={MOBILE_TOKENS.textMuted} mb={8}>
        {title}
      </Text>
      {children}
    </section>
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
    <Stack gap="md">
      <Title order={1} c={MOBILE_TOKENS.text}>
        Início
      </Title>

      <Section title="Treino de hoje">
        <Text fw={700} size="lg" c={MOBILE_TOKENS.text}>
          {slotName ?? (consistency.template_name ? consistency.template_name : 'Modo Treino')}
        </Text>
        <Text size="sm" c={MOBILE_TOKENS.textMuted} mt={4}>
          {consistency.answer}
        </Text>
        <Group gap={6} mt="sm">
          <Text size="sm" c={MOBILE_TOKENS.textMuted}>
            Ciclo: {fmtInt(consistency.cycle_slots_done)}/{fmtInt(consistency.cycle_slots_total)}
          </Text>
        </Group>
      </Section>

      <Section title="Recuperação">
        <Group gap={6} wrap="wrap" mb={6}>
          <StatusBadge domain="severity" code={recovery.level} size="sm" />
          <StatusBadge domain="dataQuality" code={recovery.uncertainty} size="sm" />
        </Group>
        <Text fw={600} c={MOBILE_TOKENS.text}>
          {recovery.headline}
        </Text>
        <Text size="sm" c={MOBILE_TOKENS.textMuted} mt={4}>
          Grupos prontos: {fmtInt(recovery.groups_ready)} de {fmtInt(recovery.groups_total)}
        </Text>
        {recoveryHref ? (
          <Link href={recoveryHref} className={styles.link}>
            Ver prontidão e sinais
          </Link>
        ) : null}
      </Section>

      <Section title="Próxima ação">
        {nextActions.items.length === 0 ? (
          <AllClearState>{NO_ACTIONS_MESSAGE}</AllClearState>
        ) : (
          <Stack gap="sm">
            {nextActions.items.slice(0, 1).map((insight) => (
              <InsightCard key={insight.fingerprint} insight={insight} filters={filters} compact />
            ))}
          </Stack>
        )}
        {actionsHref ? (
          <Link href={actionsHref} className={styles.link}>
            {nextActions.total_triggered > 1
              ? `Ver todas as ${fmtInt(nextActions.total_triggered)} sugestões`
              : 'Ver lista completa de sugestões'}
          </Link>
        ) : null}
      </Section>

      {/* D12: macros do dia e atalho de registro; o check-in semanal (N7) entra pelo `checkinSlot`. */}
      <NutritionTodayCard className={styles.card} />

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

      {/* CTA ao alcance do polegar: fixo acima do bottom nav, na zona inferior. */}
      <div className={styles.cta}>
        <Button
          component={Link}
          href="/modo-treino"
          size="lg"
          color="brand"
          fullWidth
          leftSection={<IconPlayerPlay size={20} />}
          aria-label={slotName ? `Iniciar treino: ${slotName}` : 'Iniciar treino'}
        >
          Iniciar treino
        </Button>
      </div>
    </Stack>
  );
}
