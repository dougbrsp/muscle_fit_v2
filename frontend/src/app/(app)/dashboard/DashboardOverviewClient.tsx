'use client';

import { Anchor, Group, List, SimpleGrid, Stack, Text, Title } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import Link from 'next/link';
import { useMemo, useState, type ReactNode } from 'react';
import { DashboardPeriodSelect } from '@/components/dashboard/DashboardPeriodSelect';
import { InsightCard } from '@/components/dashboard/InsightCard';
import { MetricValue } from '@/components/dashboard/MetricValue';
import { OverviewBlockCard } from '@/components/dashboard/OverviewBlockCard';
import { StatusBadge } from '@/components/dashboard/StatusBadge';
import { AllClearState, DashboardCardSkeleton, ErrorState, PartialState } from '@/components/dashboard/states';
import { MEASUREMENT_SITE_LABELS, WAIST_SITE } from '@/lib/body-measurement-sites';
import { dashboardKeys, fetchOverview } from '@/lib/dashboard-api';
import { dashboardHref, useDashboardFilters, type DashboardFilters } from '@/lib/dashboard-filters';
import { StrengthPanel } from './progressao/StrengthPanel';
import { fmtDate, fmtDecimal, fmtInt } from '@/lib/dashboard-format';
import {
  NO_ACTIONS_MESSAGE,
  OVERVIEW_BLOCK_TITLES,
  progressionBars,
  progressionLegend,
  reassessHoursLabel,
} from '@/lib/dashboard-overview';
import { volumeStatusDomain } from '@/lib/dashboard-status';
import type {
  BodyBlock,
  ConsistencyBlock,
  NextActionsBlock,
  PriorityMuscle,
  PriorityMusclesBlock,
  ProgressionBlock,
  ProgressionExercise,
  ProgressionStatusCount,
  RecoveryBlock,
} from '@/lib/dashboard-overview-types';
import { exerciseProgressHref, fatigueContextLabel, statusWithContextLabel } from '@/lib/exercise-progress';
import { BRAND } from '@/theme';

/**
 * `/dashboard` — a visão geral do hub (plano §7, §8.1 do brief, §11; F1.6).
 *
 * Substituição direta da tela antiga (widgets v1 `summary`/`training-trend`/
 * `rpe-distribution`/`muscle-groups-mapped-table` saíram daqui; as rotas
 * continuam existindo). Agora UM endpoint,
 * `GET /analytics/dashboard/overview`, devolve os 6 blocos já respondidos:
 *
 * - cada cartão responde UMA pergunta com uma frase (vinda do backend) e os
 *   2–3 números que a sustentam, e aponta o drill-down;
 * - "Próxima ação" mostra no máximo 2 insights com os sete campos do
 *   contrato §12 visíveis; quando nada dispara, o estado positivo é REAL
 *   ("nenhuma ação sugerida") — nunca uma sugestão inventada;
 * - nenhum score único: Progressão é contagem por status, decomponível;
 * - todo número passa por `MetricValue` (null nunca vira 0) e todo status
 *   pelo `StatusBadge` do registro único.
 *
 * Mobile-first: cartões empilhados em coluna no `base`, grade de 2/3 colunas
 * a partir de `md`/`lg`.
 */
export function DashboardOverviewClient() {
  const { filters, setFilters, windowQuery, rangeError } = useDashboardFilters();

  const consulta = useQuery({
    queryKey: dashboardKeys.overview(windowQuery),
    queryFn: () => fetchOverview(windowQuery),
  });

  const payload = consulta.data;

  return (
    <Stack gap="lg">
      <Group justify="space-between" align="flex-end" wrap="wrap">
        <div>
          <Title order={1} c={BRAND.textPrimary}>
            Visão geral
          </Title>
          <Text c={BRAND.textMuted} size="md">
            Estou evoluindo? E qual é a próxima ação? Seis blocos, cada um com a pergunta que responde.
          </Text>
        </div>
        <DashboardPeriodSelect filters={filters} onChange={setFilters} rangeError={rangeError} />
      </Group>

      {consulta.isLoading && (
        <SimpleGrid cols={{ base: 1, md: 2, lg: 3 }} spacing="md">
          {Array.from({ length: 6 }, (_, index) => (
            <DashboardCardSkeleton key={index} rows={4} />
          ))}
        </SimpleGrid>
      )}
      {consulta.isError && <ErrorState title="Não foi possível carregar a visão geral." error={consulta.error} />}

      {payload && (
        <>
          <NextActionsCard block={payload.blocks.next_actions} filters={filters} />

          <SimpleGrid cols={{ base: 1, md: 2, lg: 3 }} spacing="md">
            <ProgressionCard block={payload.blocks.progression} filters={filters} />
            <ConsistencyCard block={payload.blocks.consistency} filters={filters} />
            <PriorityMusclesCard block={payload.blocks.priority_muscles} filters={filters} />
            <RecoveryCard block={payload.blocks.recovery} filters={filters} />
            <BodyCard block={payload.blocks.body} />
          </SimpleGrid>

          {/* M97 F4: painel de força (some sem sessão de força no período). */}
          <StrengthPanel window={windowQuery} filters={filters} />

          <PartialState
            quality={payload.data_quality_overall}
            limitations={
              payload.data_quality_issues > 0
                ? [`${fmtInt(payload.data_quality_issues)} pendência(s) de qualidade nos dados registrados.`]
                : []
            }
            title="Qualidade dos dados"
          />

          <Text size="xs" c={BRAND.textMuted}>
            Período {fmtDate(payload.window.date_from)} – {fmtDate(payload.window.date_to)} ({payload.window.days}{' '}
            dias, fuso {payload.timezone}). Versões: {Object.values(payload.metric_versions).join(', ')}.
          </Text>
        </>
      )}
    </Stack>
  );
}

function hubHref(href: string, filters: DashboardFilters): string {
  return href.startsWith('/dashboard') ? dashboardHref(href, filters) : href;
}

// ---------------------------------------------------------------------------
// Próxima ação
// ---------------------------------------------------------------------------

function NextActionsCard({ block, filters }: { block: NextActionsBlock; filters: DashboardFilters }) {
  const verTodas = block.total_triggered > block.items.length;
  return (
    <OverviewBlockCard
      title={OVERVIEW_BLOCK_TITLES.next_actions}
      question={block.question}
      answer={block.answer}
      quality={block.data_quality}
      limitations={block.limitations}
      drilldownHref={hubHref(block.drilldown_href, filters)}
      drilldownLabel={
        verTodas ? `Ver todas as ${fmtInt(block.total_triggered)} sugestões` : 'Ver lista completa de sugestões'
      }
    >
      {block.items.length === 0 ? (
        <AllClearState>{NO_ACTIONS_MESSAGE}</AllClearState>
      ) : (
        <SimpleGrid cols={{ base: 1, md: block.items.length > 1 ? 2 : 1 }} spacing="md">
          {block.items.map((insight) => (
            <InsightCard key={insight.fingerprint} insight={insight} filters={filters} compact />
          ))}
        </SimpleGrid>
      )}
    </OverviewBlockCard>
  );
}

// ---------------------------------------------------------------------------
// Progressão — contagem por status, sem score
// ---------------------------------------------------------------------------

function ProgressionCard({ block, filters }: { block: ProgressionBlock; filters: DashboardFilters }) {
  const bars = useMemo(() => progressionBars(block), [block]);
  const legend = progressionLegend(bars);
  const destaque = block.counts.filter(
    (count) => count.count > 0 && ['PROGRESSING', 'PLATEAU_SUSPECTED', 'REGRESSING'].includes(count.status),
  );

  return (
    <OverviewBlockCard
      title={OVERVIEW_BLOCK_TITLES.progression}
      question={block.question}
      answer={block.answer}
      quality={block.data_quality}
      limitations={block.limitations}
      drilldownHref={hubHref(block.drilldown_href, filters)}
      drilldownLabel="Ver progressão por exercício"
    >
      {bars.length > 0 && (
        <div
          role="img"
          aria-label={`Exercícios por status: ${legend}`}
          style={{ display: 'flex', height: 12, borderRadius: 6, overflow: 'hidden', gap: 2 }}
        >
          {bars.map((bar) => (
            <div key={bar.status} style={{ flexGrow: bar.share, flexBasis: 0, background: bar.hex }} title={`${bar.label}: ${bar.count}`} />
          ))}
        </div>
      )}
      <Group gap={6} wrap="wrap">
        {bars.map((bar) => (
          <StatusBadge key={bar.status} domain="exerciseProgression" code={bar.status} size="xs" suffix={` ${bar.count}`} />
        ))}
      </Group>
      <Text size="xs" c={BRAND.textMuted}>
        {fmtInt(block.comparable_total)} de {fmtInt(block.exercises_total)} exercícios com histórico comparável.
      </Text>
      {destaque.map((count) => (
        <ProgressionStatusList key={count.status} count={count} filters={filters} />
      ))}
    </OverviewBlockCard>
  );
}

// Apresentação: quantos exercícios aparecem antes de "+N".
const PROGRESSION_PREVIEW_SIZE = 3;

function ProgressionStatusList({ count, filters }: { count: ProgressionStatusCount; filters: DashboardFilters }) {
  const [expanded, setExpanded] = useState(false);
  const hidden = count.exercises.length - PROGRESSION_PREVIEW_SIZE;
  const visible = expanded ? count.exercises : count.exercises.slice(0, PROGRESSION_PREVIEW_SIZE);

  return (
    <div>
      <Text size="xs" c={BRAND.textMuted} tt="uppercase" fw={600}>
        {count.label}
      </Text>
      <Group gap={6} wrap="wrap">
        {visible.map((exercise) => (
          <ProgressionExerciseLink key={exercise.exercise_id} exercise={exercise} statusLabel={count.label} filters={filters} />
        ))}
        {hidden > 0 && (
          <Anchor
            component="button"
            type="button"
            size="xs"
            c={BRAND.textMuted}
            onClick={() => setExpanded((value) => !value)}
            aria-expanded={expanded}
            aria-label={expanded ? 'Mostrar menos exercícios' : `Mostrar mais ${hidden} exercícios`}
          >
            {expanded ? 'Mostrar menos' : `+${hidden}`}
          </Anchor>
        )}
      </Group>
    </div>
  );
}

function ProgressionExerciseLink({
  exercise,
  statusLabel,
  filters,
}: {
  exercise: ProgressionExercise;
  statusLabel: string;
  filters: DashboardFilters;
}) {
  const contextLabel = fatigueContextLabel(exercise.fatigue_context);
  return (
    <Anchor
      component={Link}
      href={exerciseProgressHref(exercise.exercise_id, filters)}
      size="sm"
      title={statusWithContextLabel(statusLabel, exercise.fatigue_context)}
    >
      {exercise.name ?? `Exercício ${exercise.exercise_id}`}
      {/* o status vale para a condição de fadiga da última exposição */}
      {contextLabel ? ` · ${contextLabel}` : ''}
    </Anchor>
  );
}

// ---------------------------------------------------------------------------
// Consistência
// ---------------------------------------------------------------------------

function ConsistencyCard({ block, filters }: { block: ConsistencyBlock; filters: DashboardFilters }) {
  return (
    <OverviewBlockCard
      title={OVERVIEW_BLOCK_TITLES.consistency}
      question={block.question}
      answer={block.answer}
      quality={block.data_quality}
      limitations={block.limitations}
      drilldownHref={hubHref(block.drilldown_href, filters)}
      drilldownLabel="Ver treinos e ciclo"
    >
      <NumberRow label="Aderência no período">
        <MetricValue metric={pctMetric(block.rate)} digits={0} fw={600} />
      </NumberRow>
      <NumberRow label="Sessões com ficha / esperadas">
        <Group gap={4} wrap="nowrap">
          <MetricValue metric={block.planned_sessions} digits={0} showNature={false} />
          <Text size="sm" c={BRAND.textMuted}>
            /
          </Text>
          <MetricValue metric={block.expected_sessions} digits={0} />
        </Group>
      </NumberRow>
      <NumberRow label="Sessões avulsas">
        <MetricValue metric={block.unplanned_sessions} digits={0} showNature={false} />
      </NumberRow>
      {block.has_template && (
        <NumberRow label={`Ciclo${block.template_name ? ` · ${block.template_name}` : ''}`}>
          <Group gap={6} wrap="wrap">
            <Text size="sm" fw={600}>
              {fmtInt(block.cycle_slots_done)}/{fmtInt(block.cycle_slots_total)}
            </Text>
            {block.next_slot && (
              <StatusBadge domain="cycleSlot" code="next" size="xs" suffix={`: ${block.next_slot.name ?? `slot ${block.next_slot.sequence}`}`} />
            )}
          </Group>
        </NumberRow>
      )}
    </OverviewBlockCard>
  );
}

// ---------------------------------------------------------------------------
// Músculos prioritários
// ---------------------------------------------------------------------------

function PriorityMusclesCard({ block, filters }: { block: PriorityMusclesBlock; filters: DashboardFilters }) {
  // Meta do próprio Modelo por equipamento (`template_plan`): não há mínimo
  // nem máximo, as listas são "abaixo/acima do planejado".
  const planejado = block.target_source === 'template_plan';
  const grupos: Array<{ titulo: string; itens: PriorityMuscle[] }> = [
    { titulo: planejado ? 'Abaixo do planejado' : 'Abaixo do mínimo', itens: block.below_min },
    { titulo: planejado ? 'Acima do planejado' : 'Acima do máximo', itens: block.above_max },
    { titulo: 'Sem estímulo há mais de 7 dias', itens: block.frequency_lagging },
  ].filter((grupo) => grupo.itens.length > 0);

  return (
    <OverviewBlockCard
      title={OVERVIEW_BLOCK_TITLES.priority_muscles}
      question={block.question}
      answer={block.answer}
      quality={block.data_quality}
      limitations={block.limitations}
      drilldownHref={hubHref(block.drilldown_href, filters)}
      drilldownLabel="Ver grupos musculares"
    >
      <Group gap={6} wrap="wrap">
        <Text size="xs" c={BRAND.textMuted}>
          {fmtInt(block.groups_with_target)} de {fmtInt(block.groups_total)} grupos com meta
        </Text>
        <StatusBadge domain="targetSource" code={block.target_source} size="xs" />
      </Group>
      {grupos.map((grupo) => (
        <div key={grupo.titulo}>
          <Text size="xs" c={BRAND.textMuted} tt="uppercase" fw={600}>
            {grupo.titulo}
          </Text>
          <List size="sm" spacing={2} listStyleType="none">
            {grupo.itens.slice(0, 3).map((item) => (
              <List.Item key={item.muscle_group_id}>
                <Group gap={6} wrap="wrap">
                  <Anchor
                    component={Link}
                    href={dashboardHref('/dashboard/musculos', { ...filters, muscleGroupId: item.muscle_group_id })}
                    size="sm"
                  >
                    {item.muscle_group}
                  </Anchor>
                  <MetricValue metric={item.weekly_effective_sets} digits={1} showNature={false} />
                  <StatusBadge
                    domain={volumeStatusDomain(item.target_source)}
                    code={item.status}
                    size="xs"
                    withIcon={false}
                  />
                </Group>
              </List.Item>
            ))}
          </List>
        </div>
      ))}
    </OverviewBlockCard>
  );
}

// ---------------------------------------------------------------------------
// Recuperação
// ---------------------------------------------------------------------------

function RecoveryCard({ block, filters }: { block: RecoveryBlock; filters: DashboardFilters }) {
  const reavaliar = reassessHoursLabel(block.reassess_after_hours);
  return (
    <OverviewBlockCard
      title={OVERVIEW_BLOCK_TITLES.recovery}
      question={block.question}
      answer={block.answer}
      quality={block.data_quality}
      limitations={block.limitations}
      drilldownHref={hubHref(block.drilldown_href, filters)}
      drilldownLabel="Ver prontidão e sinais"
    >
      <Group gap={6} wrap="wrap">
        <StatusBadge domain="severity" code={block.level} size="xs" />
        <StatusBadge domain="dataQuality" code={block.uncertainty} size="xs" />
        {!block.allow_intensification && (
          <Text size="xs" c={BRAND.textMuted}>
            Intensificação não recomendada{block.intensification_blocked_reason ? `: ${block.intensification_blocked_reason}` : ''}
          </Text>
        )}
      </Group>
      <NumberRow label="Grupos prontos / dose reduzida / sem trabalho direto">
        <Text size="sm" fw={600}>
          {fmtInt(block.groups_ready)} / {fmtInt(block.groups_reduced_dose)} / {fmtInt(block.groups_no_direct_work)}
        </Text>
      </NumberRow>
      <NumberRow label="Sinais que concordam / divergem / sem baseline">
        <Text size="sm" fw={600}>
          {fmtInt(block.signals_agreeing)} / {fmtInt(block.signals_diverging)} / {fmtInt(block.signals_unavailable)}
        </Text>
      </NumberRow>
      <Text size="xs" c={BRAND.textMuted}>
        Janela própria de {block.window.days} dias{reavaliar ? ` · reavaliar em ${reavaliar}` : ''}.
      </Text>
    </OverviewBlockCard>
  );
}

// ---------------------------------------------------------------------------
// Corpo
// ---------------------------------------------------------------------------

function BodyCard({ block }: { block: BodyBlock }) {
  const serie = block.weight_points.filter((point) => point.ma7_kg != null);
  return (
    <OverviewBlockCard
      title={OVERVIEW_BLOCK_TITLES.body}
      question={block.question}
      answer={block.answer}
      quality={block.data_quality}
      limitations={block.limitations}
      drilldownHref={block.drilldown_href}
      drilldownLabel="Ver corpo"
    >
      {serie.length >= 2 && <WeightSparkline points={serie.map((point) => point.ma7_kg as number)} />}
      <NumberRow label="Peso (média 7 dias)">
        <MetricValue metric={block.weight_ma7_latest} digits={1} fw={600} />
      </NumberRow>
      <NumberRow label="Variação da média no período">
        <Group gap={4} wrap="nowrap">
          <MetricValue metric={block.weight_ma7_change} digits={1} showNature={false} />
          <MetricValue metric={block.weight_ma7_change_pct} digits={1} showNature={false} showQuality={false} />
        </Group>
      </NumberRow>
      <NumberRow label="Pesagens por semana">
        <MetricValue metric={block.weighings_per_week} digits={1} showNature={false} />
      </NumberRow>
      <NumberRow label={`Gordura / massa muscular${block.bioimpedance_local_date ? ` (${fmtDate(block.bioimpedance_local_date)})` : ''}`}>
        <Group gap={4} wrap="nowrap">
          <MetricValue metric={block.body_fat_percent} digits={1} />
          <MetricValue metric={block.muscle_mass_kg} digits={1} showNature={false} />
        </Group>
      </NumberRow>
      <NumberRow label={MEASUREMENT_SITE_LABELS[WAIST_SITE]}>
        <MetricValue metric={block.waist_cm} emptyHint={block.waist_cm.limitations[0] ?? 'Não disponível.'} />
      </NumberRow>
    </OverviewBlockCard>
  );
}

/** Linha da média móvel, inline e sem biblioteca: cabe em qualquer largura. */
function WeightSparkline({ points }: { points: number[] }) {
  const min = Math.min(...points);
  const max = Math.max(...points);
  const amplitude = max - min || 1;
  const largura = 100;
  const altura = 28;
  const path = points
    .map((value, index) => {
      const x = (index / (points.length - 1)) * largura;
      const y = altura - ((value - min) / amplitude) * (altura - 4) - 2;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');
  return (
    <svg
      viewBox={`0 0 ${largura} ${altura}`}
      preserveAspectRatio="none"
      width="100%"
      height={altura}
      role="img"
      aria-label={`Média de 7 dias do peso: de ${fmtDecimal(points[0], 1)} a ${fmtDecimal(points[points.length - 1], 1)} kg`}
    >
      <polyline points={path} fill="none" stroke={BRAND.orange} strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Apoio
// ---------------------------------------------------------------------------

function NumberRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <Group justify="space-between" align="center" wrap="nowrap" gap="xs">
      <Text size="xs" c={BRAND.textMuted}>
        {label}
      </Text>
      {children}
    </Group>
  );
}

/** Taxa 0–1 do backend exibida como percentual; `null` continua `null`. */
function pctMetric(metric: ConsistencyBlock['rate']) {
  return {
    ...metric,
    value: metric.value == null ? null : metric.value * 100,
    unit: '%',
  };
}
