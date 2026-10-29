'use client';

import dynamic from 'next/dynamic';
import { ChartPlaceholder } from '@/components/charts/ChartPlaceholder';
import {
  Alert,
  Badge,
  Button,
  Card,
  Group,
  List,
  SimpleGrid,
  Stack,
  Text,
  Title,
  Tooltip,
} from '@mantine/core';
import { IconInfoCircle } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import Link from 'next/link';
import { useMemo, useState } from 'react';
import { DashboardPeriodSelect } from '@/components/dashboard/DashboardPeriodSelect';
import { MetricValue } from '@/components/dashboard/MetricValue';
import { StatusBadge } from '@/components/dashboard/StatusBadge';
import { DashboardCardSkeleton, EmptyState, ErrorState, PartialState } from '@/components/dashboard/states';
import { dashboardKeys, fetchBody } from '@/lib/dashboard-api';
import {
  checkVisual,
  collectBodyLimitations,
  measurementChartRows,
  milestonesInRange,
  performanceChartRows,
  RECOMPOSITION_VISUAL,
  sharedTimeDomain,
  weightChartRows,
} from '@/lib/dashboard-body';
import { MEASUREMENT_SITE_LABELS, WAIST_SITE } from '@/lib/body-measurement-sites';
import type {
  BodyBioimpedance,
  BodyMeasurementSeries,
  BodyMilestone,
  BodyPerformance,
  BodyResponse,
  BodyWaist,
  BodyWeight,
  RecompositionSignal,
} from '@/lib/dashboard-body-types';
import { useDashboardFilters } from '@/lib/dashboard-filters';
import { fmtDate, fmtDecimal } from '@/lib/dashboard-format';
import { BRAND } from '@/theme';

// COD-14: recharts sob demanda; o placeholder tem a altura do gráfico.
const PesoChart = dynamic(() => import('./CorpoCharts').then((m) => m.PesoChart), {
  ssr: false,
  loading: () => <ChartPlaceholder height={240} />,
});
const DesempenhoChart = dynamic(() => import('./CorpoCharts').then((m) => m.DesempenhoChart), {
  ssr: false,
  loading: () => <ChartPlaceholder height={240} />,
});
const MedidaChart = dynamic(() => import('./CorpoCharts').then((m) => m.MedidaChart), {
  ssr: false,
  loading: () => <ChartPlaceholder height={240} />,
});

/**
 * `/dashboard/corpo` — "como está o corpo?" (plano §7, §11; etapa F1.4).
 *
 * Tudo vem pronto de `GET /analytics/dashboard/body`: peso com média móvel de
 * 7 dias (um ponto por dia COM pesagem, fonte no tooltip), cintura e demais
 * medidas por fita, bioimpedância com selo "Estimado", sinal de recomposição
 * com evidências e limitações, e o e1RM médio dos exercícios comparáveis num
 * gráfico ALINHADO no tempo (eixo X compartilhado, eixo Y próprio — nunca
 * dual-axis). Nada é recalculado aqui.
 *
 * Esta tela só exibe: peso, bioimpedância e medidas por fita são registrados
 * em `/indicadores-fisicos` (o formulário de fita, que grava em
 * `POST /body-measurements`, mora lá em `MedidasFitaSection` e invalida
 * `dashboardKeys.bodyAll()`, então este painel se atualiza sozinho).
 *
 * Esta tela SUCEDE `/evolucao-corporal`, aposentada na F2.4: aquele caminho
 * agora redireciona para cá (301 na borda + `LegacyPathRedirect` no fallback).
 */

export function DashboardCorpoClient() {
  const { filters, setFilters, windowQuery, rangeError } = useDashboardFilters();

  const consulta = useQuery({
    queryKey: dashboardKeys.body(windowQuery),
    queryFn: () => fetchBody(windowQuery),
  });

  const payload = consulta.data;
  const limitacoes = useMemo(() => (payload ? collectBodyLimitations(payload) : []), [payload]);
  const dominio = useMemo(
    () => (payload ? sharedTimeDomain(payload.window.date_from, payload.window.date_to) : null),
    [payload],
  );
  const marcos = useMemo(
    () => (payload ? milestonesInRange(payload.milestones, payload.window.date_from, payload.window.date_to) : []),
    [payload],
  );

  return (
    <Stack gap="lg">
      <Group justify="space-between" align="flex-end" wrap="wrap">
        <div>
          <Title order={2} c={BRAND.textPrimary}>
            Corpo
          </Title>
          <Text size="sm" c={BRAND.textMuted}>
            Peso com média móvel, cintura e medidas, bioimpedância estimada e o sinal de recomposição.
          </Text>
        </div>
        <DashboardPeriodSelect filters={filters} onChange={setFilters} rangeError={rangeError} />
      </Group>

      {consulta.isLoading && <DashboardCardSkeleton rows={8} />}
      {consulta.isError && <ErrorState title="Não foi possível carregar o corpo." error={consulta.error} />}

      {payload && dominio && (
        <>
          <RecomposicaoCard sinal={payload.recomposition} />

          <PartialState quality={payload.data_quality} limitations={limitacoes} />

          <PesoCard peso={payload.weight} dominio={dominio} marcos={marcos} />

          <DesempenhoCard desempenho={payload.performance} dominio={dominio} />

          <CinturaCard cintura={payload.waist} dominio={dominio} />

          <MedidasCard series={payload.measurements} dominio={dominio} />

          <BioimpedanciaCard bio={payload.bioimpedance} />

          <MarcosCard marcos={marcos} limitacoes={payload.milestones_limitations} />

          <Alert color="gray" variant="light" icon={<IconInfoCircle size={18} />}>
            Peso, bioimpedância e medidas por fita são registrados em{' '}
            <Text component={Link} href="/indicadores-fisicos" fw={600} c={BRAND.orange} span>
              Indicadores físicos
            </Text>
            , que também guarda a série histórica completa (IMC, gordura visceral e as demais
            colunas da balança). A tela antiga &quot;Evolução corporal&quot; foi aposentada na
            F2.4 e agora redireciona para cá.
          </Alert>
        </>
      )}
    </Stack>
  );
}

// ---------------------------------------------------------------------------
// Cartões
// ---------------------------------------------------------------------------

function CardBase({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <Card withBorder radius="lg" p="md" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
      <Stack gap="sm">
        <div>
          <Title order={3} size="h4" c={BRAND.textPrimary}>
            {title}
          </Title>
          {hint && (
            <Text size="sm" c={BRAND.textMuted}>
              {hint}
            </Text>
          )}
        </div>
        {children}
      </Stack>
    </Card>
  );
}

function NumberRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <Group justify="space-between" gap="xs" wrap="nowrap">
      <Text size="sm" c={BRAND.textMuted}>
        {label}
      </Text>
      {children}
    </Group>
  );
}

function RecomposicaoCard({ sinal }: { sinal: RecompositionSignal }) {
  const visual = RECOMPOSITION_VISUAL[sinal.status];
  return (
    <CardBase
      title="Sinal de recomposição"
      hint={`Janela própria de ${sinal.window.days} dias (${fmtDate(sinal.window.date_from)} a ${fmtDate(sinal.window.date_to)}). Inferido — nunca uma prova.`}
    >
      <Group gap="sm" wrap="wrap">
        <Tooltip label={visual.description} withArrow multiline w={300}>
          <Badge color={visual.color} variant="light" size="lg" style={{ cursor: 'help' }}>
            {visual.label}
          </Badge>
        </Tooltip>
        <StatusBadge domain="dataNature" code={sinal.nature} size="sm" />
        <StatusBadge domain="dataQuality" code={sinal.data_quality} size="sm" />
      </Group>
      <Text size="sm" c={BRAND.textPrimary}>
        {sinal.answer}
      </Text>
      <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="sm">
        {sinal.checks.map((check) => {
          const cv = checkVisual(check.result);
          return (
            <Card key={check.code} withBorder radius="md" p="sm" style={{ borderColor: BRAND.border }}>
              <Group justify="space-between" gap="xs">
                <Text size="sm" fw={600}>
                  {check.label}
                </Text>
                <Badge color={cv.color} variant="light" size="sm">
                  {cv.label}
                </Badge>
              </Group>
              <Text size="xs" c={BRAND.textMuted} mt={4}>
                {check.detail}
              </Text>
            </Card>
          );
        })}
      </SimpleGrid>
      {sinal.evidence.length > 0 && (
        <Text size="xs" c={BRAND.textMuted}>
          Evidências:{' '}
          {sinal.evidence
            .map((e) =>
              e.from_value != null
                ? `${e.metric} ${fmtDecimal(e.from_value, 1)} → ${fmtDecimal(e.to_value, 1)} ${e.unit ?? ''}`
                : `${e.metric} ${fmtDecimal(e.to_value, 1)} ${e.unit ?? ''}`,
            )
            .join(' · ')}
        </Text>
      )}
      <PartialState quality={null} limitations={sinal.limitations} title="Limitações" fixHref={null} />
      {sinal.alternative_explanations.length > 0 && (
        <List size="xs" c={BRAND.textMuted}>
          {sinal.alternative_explanations.map((alt) => (
            <List.Item key={alt}>{alt}</List.Item>
          ))}
        </List>
      )}
    </CardBase>
  );
}

function PesoCard({ peso, dominio, marcos }: { peso: BodyWeight; dominio: [number, number]; marcos: BodyMilestone[] }) {
  const rows = useMemo(() => weightChartRows(peso.points), [peso.points]);
  return (
    <CardBase title="Peso" hint={peso.answer}>
      <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="xs">
        <NumberRow label="Última pesagem">
          <MetricValue metric={peso.latest} digits={1} fw={600} />
        </NumberRow>
        <NumberRow label="Média de 7 dias">
          <MetricValue metric={peso.ma7_latest} digits={1} fw={600} />
        </NumberRow>
        <NumberRow label="Variação da média no período">
          <Group gap={4} wrap="nowrap">
            <MetricValue metric={peso.change} digits={1} showNature={false} />
            <MetricValue metric={peso.change_pct} digits={1} showNature={false} showQuality={false} />
          </Group>
        </NumberRow>
        <NumberRow label="Pesagens por semana">
          <MetricValue metric={peso.weighings_per_week} digits={1} showNature={false} />
        </NumberRow>
      </SimpleGrid>
      {rows.length === 0 ? (
        <EmptyState description="Nenhuma pesagem no período. Dias sem pesagem não viram zero." />
      ) : (
        <PesoChart rows={rows} dominio={dominio} marcos={marcos} />
      )}
      <PartialState quality={peso.data_quality} limitations={peso.limitations} fixHref={null} />
    </CardBase>
  );
}

function DesempenhoCard({ desempenho, dominio }: { desempenho: BodyPerformance; dominio: [number, number] }) {
  const rows = useMemo(() => performanceChartRows(desempenho.points), [desempenho.points]);
  return (
    <CardBase
      title="Desempenho (e1RM médio dos comparáveis)"
      hint={`${desempenho.progressing_or_stable} de ${desempenho.comparable_exercises} exercícios comparáveis progredindo ou estáveis. Só entram as execuções comparáveis de cada exercício (mesmo equipamento e convenção de carga) e o 1RM estimado de séries com até 12 repetições. Mesmo eixo de tempo do peso; eixo próprio.`}
    >
      {rows.length === 0 ? (
        <EmptyState description="Nenhum exercício com histórico comparável no período." />
      ) : (
        <DesempenhoChart rows={rows} dominio={dominio} />
      )}
      <PartialState quality={desempenho.data_quality} limitations={desempenho.limitations} fixHref={null} />
    </CardBase>
  );
}

function CinturaCard({ cintura, dominio }: { cintura: BodyWaist; dominio: [number, number] }) {
  const rows = useMemo(() => measurementChartRows(cintura.points), [cintura.points]);
  return (
    <CardBase
      title={MEASUREMENT_SITE_LABELS[WAIST_SITE]}
      hint="Última medida NO PROTOCOLO e a variação entre a primeira e a última no protocolo dentro do período. Medidas fora do protocolo aparecem como pontos e não entram no sinal."
    >
      <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="xs">
        <NumberRow label={`Última (no protocolo${cintura.latest_local_date ? `, ${fmtDate(cintura.latest_local_date)}` : ''})`}>
          <MetricValue metric={cintura.latest} digits={1} fw={600} emptyHint={cintura.limitations[0] ?? 'Sem medida.'} />
        </NumberRow>
        <NumberRow label={`Variação${cintura.change_span_days ? ` em ${cintura.change_span_days} dias` : ''}`}>
          <MetricValue metric={cintura.change} digits={1} showNature={false} emptyHint={cintura.limitations[0] ?? 'Sem variação.'} />
        </NumberRow>
        <NumberRow label="Medidas (protocolo / fora)">
          <Text size="sm" fw={600}>
            {cintura.protocol_points} / {cintura.out_of_protocol_points}
          </Text>
        </NumberRow>
      </SimpleGrid>
      {rows.length === 0 ? (
        <EmptyState
          title={cintura.table_available ? 'Nenhuma medida de cintura no período' : 'Medidas indisponíveis'}
          description={cintura.limitations[0]}
        />
      ) : (
        <MedidaChart rows={rows} dominio={dominio} />
      )}
      <PartialState quality={cintura.data_quality} limitations={cintura.available ? cintura.limitations : []} fixHref={null} />
    </CardBase>
  );
}

function MedidasCard({ series, dominio }: { series: BodyMeasurementSeries[]; dominio: [number, number] }) {
  const [site, setSite] = useState<string | null>(null);
  const escolhida = series.find((s) => s.site === site) ?? series[0];
  const rows = useMemo(() => (escolhida ? measurementChartRows(escolhida.points) : []), [escolhida]);
  if (series.length === 0) {
    return (
      <CardBase title="Outras medidas" hint="Demais locais medidos por fita.">
        <EmptyState
          description="Nenhuma outra medida no período."
          actionHref="/indicadores-fisicos"
          actionLabel="Registrar medida em Indicadores físicos"
        />
      </CardBase>
    );
  }
  return (
    <CardBase title="Outras medidas" hint="Demais locais medidos por fita — séries simples por local.">
      <Group gap="xs" wrap="wrap">
        {series.map((s) => (
          <Button
            key={s.site}
            size="xs"
            variant={escolhida?.site === s.site ? 'filled' : 'light'}
            color="brand"
            onClick={() => setSite(s.site)}
          >
            {s.label}
          </Button>
        ))}
      </Group>
      {escolhida && (
        <>
          <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="xs">
            <NumberRow label={`Última (${fmtDate(escolhida.latest_local_date)})`}>
              <MetricValue metric={escolhida.latest} digits={1} fw={600} />
            </NumberRow>
            <NumberRow label="Variação no período">
              <MetricValue metric={escolhida.change} digits={1} showNature={false} />
            </NumberRow>
          </SimpleGrid>
          <MedidaChart rows={rows} dominio={dominio} />
        </>
      )}
    </CardBase>
  );
}

function BioimpedanciaCard({ bio }: { bio: BodyBioimpedance }) {
  return (
    <CardBase
      title="Bioimpedância"
      hint={`Estimativa da balança${bio.latest_local_date ? ` · última leitura ${fmtDate(bio.latest_local_date)}` : ''} · ${bio.readings_count} leitura(s) no período.`}
    >
      <Group gap="xs">
        <StatusBadge domain="dataNature" code="ESTIMATED" size="sm" />
        <StatusBadge domain="dataQuality" code={bio.data_quality} size="sm" />
      </Group>
      <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="xs">
        <NumberRow label="Gordura corporal">
          <MetricValue metric={bio.body_fat_percent} digits={1} fw={600} showNature={false} />
        </NumberRow>
        <NumberRow label="Massa muscular">
          <MetricValue metric={bio.muscle_mass_kg} digits={1} fw={600} showNature={false} />
        </NumberRow>
        <NumberRow label="Água corporal">
          <MetricValue metric={bio.body_water_percent} digits={1} fw={600} showNature={false} />
        </NumberRow>
        <NumberRow label="Gordura: variação no período">
          <MetricValue metric={bio.body_fat_change} digits={1} showNature={false} emptyHint="Uma leitura isolada não confirma tendência." />
        </NumberRow>
        <NumberRow label="Massa muscular: variação">
          <MetricValue metric={bio.muscle_mass_change} digits={1} showNature={false} emptyHint="Uma leitura isolada não confirma tendência." />
        </NumberRow>
      </SimpleGrid>
      <PartialState quality={bio.data_quality} limitations={bio.limitations} fixHref={null} />
    </CardBase>
  );
}

function MarcosCard({ marcos, limitacoes }: { marcos: BodyMilestone[]; limitacoes: string[] }) {
  return (
    <CardBase title="Marcos e mudanças de fase" hint="Linhas tracejadas no gráfico de peso.">
      {marcos.length === 0 ? (
        <Text size="sm" c={BRAND.textMuted}>
          Nenhum marco no período.
        </Text>
      ) : (
        <List size="sm">
          {marcos.map((m) => (
            <List.Item key={`${m.kind}-${m.local_date}-${m.label}`}>
              <Text span fw={600}>
                {fmtDate(m.local_date)}
              </Text>{' '}
              — {m.label}
              {m.detail ? ` (${m.detail})` : ''}
            </List.Item>
          ))}
        </List>
      )}
      <PartialState quality={null} limitations={limitacoes} title="Limitações" fixHref={null} />
    </CardBase>
  );
}
