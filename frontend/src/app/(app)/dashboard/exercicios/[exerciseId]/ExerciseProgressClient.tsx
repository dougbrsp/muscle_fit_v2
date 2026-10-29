'use client';

import dynamic from 'next/dynamic';
import { ChartPlaceholder } from '@/components/charts/ChartPlaceholder';
import {
  Anchor,
  Badge,
  Box,
  Divider,
  Group,
  Paper,
  SegmentedControl,
  Stack,
  Table,
  Text,
  Title,
  Tooltip,
} from '@mantine/core';
import { IconArrowLeft, IconBarbell, IconInfoCircle } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useMemo, useState } from 'react';
import { DashboardPeriodSelect } from '@/components/dashboard/DashboardPeriodSelect';
import { MetricValue } from '@/components/dashboard/MetricValue';
import { BADGE_QUEBRA_LINHA, StatusBadge } from '@/components/dashboard/StatusBadge';
import {
  DashboardCardSkeleton,
  DashboardSkeleton,
  EmptyState,
  ErrorState,
  PartialState,
} from '@/components/dashboard/states';
import { CHART_COLORS, CHART_LEGEND_STYLE } from '@/components/charts/chartTheme';
import { ApiError } from '@/lib/api';
import { dashboardKeys, fetchExerciseProgress } from '@/lib/dashboard-api';
import { dashboardHref, useDashboardFilters } from '@/lib/dashboard-filters';
import {
  EM_DASH,
  fmtDate,
  fmtDecimal,
  fmtInt,
  fmtKg,
  fmtRpe,
  fmtSignedPercent,
} from '@/lib/dashboard-format';
import type { Exposure, ExerciseProgressResponse } from '@/lib/dashboard-v3-types';
import {
  COMPARABILITY_MISMATCH_HINTS,
  COMPARABILITY_MISMATCH_LABELS,
  buildExposurePair,
  buildRpeSeries,
  buildTrendSeries,
  buildUnifiedE1rmChart,
  comparabilityMismatch,
  decisionsForKey,
  e1rmReliabilityNote,
  fatigueContextLabel,
  progressionDecisionLabel,
  statusReasonsText,
} from '@/lib/exercise-progress';
import { loadConventionLabel } from '@/lib/equipment-exercises-types';
import {
  defaultGoalValue,
  goalQueryParam,
  isHighlightedGoal,
  progressionGoalOptions,
  progressionGoalSelectValue,
} from '@/lib/training-goal-filter';
import { useTrainingGoals } from '@/lib/training-goals';
import { useDynamicRouteParam } from '@/lib/use-dynamic-route-param';
import { BRAND } from '@/theme';

// COD-14: recharts sob demanda; o placeholder tem a altura do gráfico.
const TrendChart = dynamic(() => import('./ExerciseProgressCharts').then((m) => m.TrendChart), {
  ssr: false,
  loading: () => <ChartPlaceholder height="100%" />,
});
const RpeChart = dynamic(() => import('./ExerciseProgressCharts').then((m) => m.RpeChart), {
  ssr: false,
  loading: () => <ChartPlaceholder height="100%" />,
});
const UnifiedE1rmChart = dynamic(
  () => import('@/components/charts/UnifiedE1rmChart').then((m) => m.UnifiedE1rmChart),
  { ssr: false, loading: () => <ChartPlaceholder height="100%" /> },
);

/**
 * `/dashboard/exercicios/[exerciseId]` — "estou evoluindo NESTE exercício, em
 * condições comparáveis?" (plano §7, §8.2, §11; etapa F1.3).
 *
 * Fonte única: `GET /analytics/dashboard/exercises/{id}/progress`. Esta tela
 * NÃO classifica progressão, NÃO calcula e1RM e NÃO decide comparabilidade:
 * status, razões, e1RM, recordes e a `comparability_key` de cada exposição
 * chegam prontos do `exposure_service`. O que existe em `lib/exercise-progress.ts`
 * é tradução de código para frase e montagem de vista (par anterior/atual e a
 * série do gráfico sem as exposições não comparáveis).
 *
 * A decisão central da tela: o que NÃO é comparável fica visível em vez de ser
 * escondido. Uma troca de aparelho aparece marcada na tabela e fora do gráfico
 * — é exatamente a informação que impede a conclusão errada ("caí de 80 para
 * 40 kg" quando na verdade mudou de máquina).
 */
export function ExerciseProgressClient() {
  const params = useParams<{ exerciseId: string }>();
  // `useParams()` devolve o slug `placeholder` no export estático; o id real
  // está na barra de endereços (ver `use-dynamic-route-param.ts`).
  const rawId = useDynamicRouteParam(params?.exerciseId);
  const exerciseId = rawId != null && /^\d+$/.test(rawId) ? Number(rawId) : null;

  const { filters, setFilters, windowQuery, rangeError } = useDashboardFilters();
  // M97 F4: a progressão é separada por objetivo (o backend filtra); o e1RM e
  // o recorde são únicos e chegam iguais nos dois.
  // `null` = o objetivo default do backend (sem `?goal=`); rótulos de `/training-goals`.
  const [goal, setGoal] = useState<string | null>(null);
  const goals = useTrainingGoals().data;
  const goalSelected = progressionGoalSelectValue(goals, goal);

  const query = useQuery({
    queryKey: dashboardKeys.exerciseProgress(exerciseId, windowQuery, goal),
    queryFn: () => fetchExerciseProgress(exerciseId, windowQuery, undefined, goal),
    enabled: exerciseId != null,
    retry: (failureCount, error) =>
      error instanceof ApiError && error.status === 404 ? false : failureCount < 2,
  });

  const notFound =
    (query.error instanceof ApiError && query.error.status === 404) ||
    (rawId != null && exerciseId == null);

  const voltar = dashboardHref('/dashboard/progressao', filters);

  return (
    <Stack gap="lg">
      <Group justify="space-between" align="flex-end" wrap="wrap" gap="sm">
        <Anchor component={Link} href={voltar} size="sm" c={BRAND.orange}>
          <Group gap={4} wrap="nowrap">
            <IconArrowLeft size={14} />
            Voltar para Progressão
          </Group>
        </Anchor>
        <Group gap="sm" align="flex-end" wrap="wrap">
          {goals && goalSelected && (
            <Stack gap={4}>
              <Text size="sm" fw={500}>
                Objetivo da progressão
              </Text>
              <SegmentedControl
                data={progressionGoalOptions(goals)}
                value={goalSelected}
                onChange={(value) => setGoal(goalQueryParam(goals, value))}
              />
            </Stack>
          )}
          <DashboardPeriodSelect filters={filters} onChange={setFilters} rangeError={rangeError} />
        </Group>
      </Group>

      {notFound && (
        <ErrorState
          title="Exercício não encontrado"
          description="Este exercício não existe no catálogo canônico. Verifique o link ou escolha outro exercício na tela de Progressão."
        />
      )}

      {!notFound && (exerciseId == null || query.isLoading) && (
        <Stack gap="lg">
          <DashboardSkeleton rows={3} />
          <DashboardCardSkeleton rows={4} />
          <DashboardCardSkeleton rows={6} />
        </Stack>
      )}

      {!notFound && query.isError && (
        <ErrorState title="Não foi possível carregar o progresso deste exercício." error={query.error} />
      )}

      {!notFound && query.data && (
        <ExerciseProgressContent data={query.data} defaultGoal={defaultGoalValue(goals)} />
      )}
    </Stack>
  );
}

function ExerciseProgressContent({ data, defaultGoal }: { data: ExerciseProgressResponse; defaultGoal: string | null }) {
  const currentKey = data.comparability_key;
  const reasons = useMemo(() => statusReasonsText(data.status_reasons), [data.status_reasons]);
  const pair = useMemo(() => buildExposurePair(data), [data]);
  const trend = useMemo(() => buildTrendSeries(data.exposures, currentKey), [data.exposures, currentKey]);
  // M35: RPE médio POR SESSÃO — o que `/exercicios` tinha e o hub não.
  const rpe = useMemo(() => buildRpeSeries(data.exposures), [data.exposures]);
  // M97 F4: linha ÚNICA de e1RM (todos os objetivos) + marcas de deload.
  const unified = useMemo(
    () =>
      buildUnifiedE1rmChart(data.e1rm_series, data.deload_sessions, (value) => isHighlightedGoal(value, defaultGoal)),
    [data.e1rm_series, data.deload_sessions, defaultGoal],
  );
  const decisions = useMemo(
    () => decisionsForKey(data.progression_decisions, currentKey),
    [data.progression_decisions, currentKey],
  );

  const nome = data.exercise.name || data.exercise.amazfit_name || `Exercício ${data.exercise.exercise_id}`;
  const ultima = data.exposures.length > 0 ? data.exposures[data.exposures.length - 1] : null;
  // A convenção assumida por falta de cadastro é dito explícito: a leitura de
  // "20 kg" muda conforme a convenção, então omitir isso seria enganoso.
  const convencaoAssumida = ultima?.load_convention_source === 'default_total';

  return (
    <Stack gap="lg">
      <Paper withBorder radius="lg" p="md" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
        <Group justify="space-between" align="flex-start" wrap="wrap" gap="sm">
          <div style={{ flex: '1 1 260px' }}>
            <Title order={2} c={BRAND.textPrimary}>
              {nome}
            </Title>
            <Group gap="xs" mt={6} wrap="wrap">
              {/* F1.7: nome de equipamento é texto do usuário e costuma ser
                  longo ("Banco supino inclinado 45°"); sem isto o Badge o
                  cortava com reticências num celular. */}
              <Badge
                variant="light"
                color="gray"
                radius="sm"
                leftSection={<IconBarbell size={12} />}
                styles={BADGE_QUEBRA_LINHA}
              >
                {ultima?.equipment_name || 'Equipamento não identificado'}
              </Badge>
              <Tooltip
                label={
                  convencaoAssumida
                    ? 'Convenção não cadastrada para este par equipamento/exercício: o sistema assume carga total (D12).'
                    : 'Como ler o número de carga registrado neste par equipamento/exercício.'
                }
                multiline
                w={300}
                withArrow
              >
                <Badge
                  variant="outline"
                  color={convencaoAssumida ? 'orange' : 'gray'}
                  radius="sm"
                  styles={BADGE_QUEBRA_LINHA}
                >
                  {loadConventionLabel(currentKey?.load_convention ?? null)}
                  {convencaoAssumida ? ' (assumida)' : ''}
                </Badge>
              </Tooltip>
            </Group>
          </div>
          <Stack gap={4} align="flex-end">
            <StatusBadge domain="exerciseProgression" code={data.status} size="lg" />
            {/* O status vale para o contexto de fadiga da exposição mais
                recente ("Progresso · pré-fatigado"); rótulo do backend. */}
            {fatigueContextLabel(data.fatigue_context) && (
              <Text size="xs" c={BRAND.textMuted}>
                Condição: {fatigueContextLabel(data.fatigue_context)}
              </Text>
            )}
          </Stack>
        </Group>

        {reasons.length > 0 && (
          <Stack gap={4} mt="md">
            <Text size="sm" fw={600} c={BRAND.textPrimary}>
              Por que este status:
            </Text>
            {reasons.map((reason) => (
              <Text key={reason.code} size="sm" c={BRAND.textMuted}>
                • {reason.text}
              </Text>
            ))}
          </Stack>
        )}

        <Text size="xs" c="dimmed" mt="sm">
          Período de {fmtDate(data.window.date_from)} a {fmtDate(data.window.date_to)} ·{' '}
          {fmtInt(data.exposures.length)} exposição(ões) · regra{' '}
          {data.metric_versions.exercise_progression_status ?? 'desconhecida'}
        </Text>
      </Paper>

      <PartialState quality={data.data_quality} limitations={data.limitations} />

      {data.status === 'INSUFFICIENT_DATA' && (
        <EmptyState
          title="Ainda não dá para dizer se você está evoluindo"
          description="A comparação exige DUAS exposições comparáveis — mesmo exercício, mesmo equipamento e mesma convenção de carga — dentro do período selecionado. Registre mais uma sessão neste exercício (ou amplie o período) para que a leitura apareça."
        />
      )}

      {pair && <ExposurePairCard pair={pair} />}

      <TrendCard trend={trend} />

      <UnifiedE1rmCard chart={unified} />

      <RpeCard rpe={rpe} />

      <RecordsCard data={data} />

      <HistoryCard exposures={data.exposures} />

      {decisions.length > 0 && <DecisionsCard decisions={decisions} />}
    </Stack>
  );
}

// ---------------------------------------------------------------------------
// Última exposição comparável vs. a anterior
// ---------------------------------------------------------------------------

function DeltaText({
  deltaAbs,
  deltaPct,
  betterWhen,
}: {
  deltaAbs: number | null;
  deltaPct: number | null;
  betterWhen: 'up' | 'down' | 'neutral';
}) {
  if (deltaAbs == null && deltaPct == null) {
    return (
      <Text size="sm" c="dimmed">
        {EM_DASH}
      </Text>
    );
  }
  const direcao = (deltaAbs ?? 0) > 0 ? 'up' : (deltaAbs ?? 0) < 0 ? 'down' : 'flat';
  const cor =
    betterWhen === 'neutral' || direcao === 'flat'
      ? BRAND.textMuted
      : direcao === betterWhen
        ? CHART_COLORS.positive
        : CHART_COLORS.negative;
  return (
    <Text size="sm" fw={600} c={cor}>
      {deltaPct != null ? fmtSignedPercent(deltaPct) : `${(deltaAbs ?? 0) > 0 ? '+' : ''}${deltaAbs}`}
    </Text>
  );
}

function ExposurePairCard({ pair }: { pair: NonNullable<ReturnType<typeof buildExposurePair>> }) {
  return (
    <Paper withBorder radius="lg" p="md" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
      <Text fw={700} c={BRAND.textPrimary}>
        Última exposição vs. a anterior
      </Text>
      <Text size="sm" c={BRAND.textMuted}>
        {fmtDate(pair.previous?.local_date)} → {fmtDate(pair.current.local_date)}
      </Text>

      {!pair.comparable && (
        <Text size="sm" c={BRAND.orange} mt="xs">
          {pair.previous == null
            ? 'Não há exposição anterior no período: os números da última execução estão abaixo, sem variação.'
            : `Estas duas execuções não são comparáveis (${COMPARABILITY_MISMATCH_LABELS[
                pair.mismatch as keyof typeof COMPARABILITY_MISMATCH_LABELS
              ]}). Os valores continuam visíveis, mas a variação seria uma conclusão errada — por isso ela não é exibida.`}
        </Text>
      )}

      {/*
        F1.7 — esta era a única tabela do hub sem `Table.ScrollContainer`: as 4
        colunas (métrica + 3 números com unidade e selos) pedem ~320px de
        largura mínima, e num celular ela empurrava a PÁGINA INTEIRA para o
        lado. Com o container, a rolagem fica dentro da tabela e a página nunca
        rola na horizontal.
      */}
      <Table.ScrollContainer minWidth={340} mt="md">
        <Table verticalSpacing="xs" withRowBorders={false}>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Métrica</Table.Th>
              <Table.Th>Anterior</Table.Th>
              <Table.Th>Atual</Table.Th>
              <Table.Th>Variação</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {pair.rows.map((row) => (
              <Table.Tr key={row.code}>
                <Table.Td>
                  <Text size="sm" c={BRAND.textPrimary}>
                    {row.label}
                  </Text>
                  {row.note && (
                    <Text size="xs" c={BRAND.textMuted}>
                      {row.note}
                    </Text>
                  )}
                </Table.Td>
                <Table.Td>
                  <MetricValue metric={row.previous} digits={row.code === 'e1rm' ? 1 : undefined} showNature={false} />
                  {row.previousDetail && (
                    <Text size="xs" c={BRAND.textMuted}>
                      {row.previousDetail}
                    </Text>
                  )}
                </Table.Td>
                <Table.Td>
                  <MetricValue metric={row.current} fw={600} />
                  {row.currentDetail && (
                    <Text size="xs" c={BRAND.textMuted}>
                      {row.currentDetail}
                    </Text>
                  )}
                </Table.Td>
                <Table.Td>
                  <DeltaText deltaAbs={row.deltaAbs} deltaPct={row.deltaPct} betterWhen={row.betterWhen} />
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </Table.ScrollContainer>
    </Paper>
  );
}

// ---------------------------------------------------------------------------
// Tendência (só exposições comparáveis, eixo Y único)
// ---------------------------------------------------------------------------

function UnifiedE1rmCard({ chart }: { chart: ReturnType<typeof buildUnifiedE1rmChart> }) {
  // Sem força nem deload a linha única repete a tendência acima: não duplica.
  if (!chart.hasSeries || (chart.strengthPoints === 0 && chart.deloadSessions === 0)) return null;
  return (
    <Paper withBorder radius="lg" p="md" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
      <Text fw={700} c={BRAND.textPrimary}>
        1RM estimado — todos os objetivos
      </Text>
      <Text size="sm" c={BRAND.textMuted}>
        O 1RM estimado é um só para hipertrofia e força. Pontos destacados: sessões de força. Linhas tracejadas:
        sessões de deload (fora da linha e do recorde).
      </Text>
      <Box h={280} mt="md">
        <UnifiedE1rmChart chart={chart} />
      </Box>
      <Text size="xs" style={CHART_LEGEND_STYLE} mt={4}>
        {fmtInt(chart.strengthPoints)} ponto(s) de força · {fmtInt(chart.deloadSessions)} sessão(ões) de deload
        {chart.otherKeyPoints > 0
          ? ` · ${fmtInt(chart.otherKeyPoints)} ponto(s) em outro equipamento/convenção ficaram de fora.`
          : '.'}
      </Text>
    </Paper>
  );
}

function TrendCard({ trend }: { trend: ReturnType<typeof buildTrendSeries> }) {
  return (
    <Paper withBorder radius="lg" p="md" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
      <Text fw={700} c={BRAND.textPrimary}>
        Tendência {trend.metric ? `— ${trend.label}` : ''}
      </Text>
      <Text size="sm" c={BRAND.textMuted}>
        Somente exposições comparáveis com a chave atual (mesmo equipamento e mesma convenção de carga).
      </Text>

      {trend.metric == null ? (
        <Text size="sm" c="dimmed" mt="md">
          Ainda não há duas exposições comparáveis com valor para desenhar uma linha.
        </Text>
      ) : (
        <>
          <Box h={280} mt="md">
            <TrendChart trend={trend} />
          </Box>
          <Text size="xs" style={CHART_LEGEND_STYLE} mt={4}>
            {trend.metric === 'e1rm'
              ? '1RM estimado (Epley + RIR): estimativa, não medida.'
              : 'Série única, eixo único.'}
            {trend.excluded > 0
              ? ` ${fmtInt(trend.excluded)} exposição(ões) ficaram de fora por não serem comparáveis.`
              : ''}
          </Text>
        </>
      )}
    </Paper>
  );
}

// ---------------------------------------------------------------------------
// RPE médio por SESSÃO (F2.5 / M35)
// ---------------------------------------------------------------------------

/**
 * O que a tela legada `/exercicios` mostrava e o hub não: o esforço percebido
 * médio de cada sessão deste exercício.
 *
 * Três decisões declaradas na própria tela:
 *
 * 1. **Só séries `working`.** O `avg_rpe` de cada exposição vem do
 *    `exposure_service`, que lê apenas séries de trabalho — aquecimento e
 *    série de ajuste ficam fora. RPE de aquecimento mede o esforço de
 *    aquecer; somá-lo puxaria a média para baixo e faria uma sessão dura
 *    parecer leve. E como o status, o e1RM e a tonelagem desta mesma tela já
 *    leem só `working`, incluir preparatórias aqui criaria dois RPE médios
 *    incompatíveis lado a lado.
 * 2. **Eixo Y único**, como todo gráfico do hub: uma série, uma grandeza.
 *    Sobrepor RPE e carga num eixo duplo é a forma clássica de sugerir
 *    correlação que o dado não sustenta.
 * 3. **Não é veredito.** RPE subindo com a mesma carga costuma indicar fadiga
 *    acumulada, mas sono, cafeína e a ordem do exercício na sessão mexem no
 *    mesmo número. Quem classifica progressão é o `status` do backend.
 */
function RpeCard({ rpe }: { rpe: ReturnType<typeof buildRpeSeries> }) {
  return (
    <Paper withBorder radius="lg" p="md" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
      <Group justify="space-between" align="flex-start" wrap="wrap" gap="sm">
        <div style={{ flex: '1 1 240px' }}>
          <Text fw={700} c={BRAND.textPrimary}>
            RPE médio por sessão
          </Text>
          <Text size="sm" c={BRAND.textMuted}>
            Média das séries de trabalho de cada sessão — aquecimento e série de ajuste ficam fora.
            Só exposições comparáveis com a chave atual.
          </Text>
        </div>
        <Stack gap={0} align="flex-end">
          <Text size="xs" c="dimmed">
            Média do período
          </Text>
          <Text size="xl" fw={700} c={BRAND.textPrimary}>
            {rpe.average != null ? fmtDecimal(rpe.average, 1) : EM_DASH}
          </Text>
        </Stack>
      </Group>

      {!rpe.hasSeries ? (
        <Text size="sm" c="dimmed" mt="md">
          Ainda não há duas sessões comparáveis com RPE registrado para desenhar uma linha.
          {rpe.missing > 0 ? ` ${fmtInt(rpe.missing)} sessão(ões) sem RPE.` : ''}
        </Text>
      ) : (
        <>
          <Box h={240} mt="md">
            <RpeChart rpe={rpe} />
          </Box>
          <Text size="xs" style={CHART_LEGEND_STYLE} mt={4}>
            RPE é relato, não medida: ele descreve o esforço percebido, e sono, cafeína e a ordem do
            exercício na sessão mexem nele tanto quanto a carga.
            {rpe.missing > 0 ? ` ${fmtInt(rpe.missing)} sessão(ões) sem RPE registrado.` : ''}
            {rpe.excluded > 0
              ? ` ${fmtInt(rpe.excluded)} exposição(ões) ficaram de fora por não serem comparáveis.`
              : ''}
          </Text>
        </>
      )}
    </Paper>
  );
}

// ---------------------------------------------------------------------------
// Recordes na mesma chave
// ---------------------------------------------------------------------------

function RecordsCard({ data }: { data: ExerciseProgressResponse }) {
  // M76 Lote 3d: sem e1RM confiável na chave, o backend manda o maior e1RM
  // de séries acima do teto de reps marcado `reliable: false`; a tela só
  // acrescenta a marca "não confiável (>N reps)".
  const linhas = [
    {
      label: 'Melhor 1RM estimado',
      record: data.records.best_e1rm,
      estimado: true,
      nota: e1rmReliabilityNote(data.records.best_e1rm),
    },
    { label: 'Melhor carga', record: data.records.best_load_kg, estimado: false, nota: null },
  ];
  return (
    <Paper withBorder radius="lg" p="md" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
      <Text fw={700} c={BRAND.textPrimary}>
        Recordes comparáveis
      </Text>
      <Text size="sm" c={BRAND.textMuted}>
        Melhor marca DENTRO da chave atual: recorde em outro aparelho não é recorde deste. O recorde é um só para
        todos os objetivos; sessões de deload não contam.
      </Text>
      <Stack gap={6} mt="md">
        {linhas.map((linha) => (
          <Group key={linha.label} gap="sm" wrap="nowrap" justify="space-between">
            <Text size="sm" c={BRAND.textPrimary}>
              {linha.label}
            </Text>
            <Group gap="xs" wrap="nowrap">
              <MetricValue
                metric={{
                  value: linha.record?.value ?? null,
                  unit: 'kg',
                  nature: linha.estimado ? 'ESTIMATED' : 'OBSERVED',
                  metric_version:
                    (linha.estimado ? data.metric_versions.e1rm : data.metric_versions.exposure) ?? 'desconhecida',
                  data_quality: data.data_quality,
                  limitations: [],
                }}
                fw={600}
                emptyHint="Nenhuma exposição comparável com este valor no período."
              />
              {linha.nota ? (
                <Text size="xs" c="yellow.5" fw={600}>
                  {linha.nota}
                </Text>
              ) : null}
              <Text size="xs" c="dimmed">
                {linha.record?.local_date ? fmtDate(linha.record.local_date) : ''}
              </Text>
            </Group>
          </Group>
        ))}
      </Stack>
    </Paper>
  );
}

// ---------------------------------------------------------------------------
// Histórico de exposições — tabela no desktop, cartões no mobile
// ---------------------------------------------------------------------------

function fmtRest(seconds: number | null): string {
  return seconds == null ? EM_DASH : `${fmtInt(seconds)} s`;
}

function setsText(exposure: Exposure): string {
  if (exposure.sets.length === 0) return EM_DASH;
  return exposure.sets
    .map(
      (serie) =>
        `${fmtKg(serie.weight_kg)} × ${fmtInt(serie.reps)}` +
        `${serie.rpe != null ? ` · RPE ${fmtRpe(serie.rpe)}` : ''}` +
        `${serie.rest_seconds != null ? ` · ${fmtRest(serie.rest_seconds)}` : ''}`,
    )
    .join(' | ');
}

function MismatchBadge({ mismatch }: { mismatch: ReturnType<typeof comparabilityMismatch> }) {
  if (mismatch == null) return null;
  return (
    <Tooltip label={COMPARABILITY_MISMATCH_HINTS[mismatch]} multiline w={320} withArrow>
      <Badge color="orange" variant="light" radius="sm" size="sm" leftSection={<IconInfoCircle size={12} />}>
        Não comparável · {COMPARABILITY_MISMATCH_LABELS[mismatch]}
      </Badge>
    </Tooltip>
  );
}

function HistoryCard({ exposures }: { exposures: Exposure[] }) {
  // Mais recente primeiro: é o que a pessoa procura ao abrir a tela.
  const linhas = useMemo(
    () =>
      [...exposures].reverse().map((exposure) => ({
        exposure,
        mismatch: comparabilityMismatch(exposure),
      })),
    [exposures],
  );

  return (
    <Paper withBorder radius="lg" p="md" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
      <Text fw={700} c={BRAND.textPrimary}>
        Histórico de exposições
      </Text>
      <Text size="sm" c={BRAND.textMuted}>
        Cada linha é uma execução do exercício numa sessão. As que não são comparáveis com a chave atual estão
        marcadas — elas ficam fora do status e do gráfico.
      </Text>

      {linhas.length === 0 ? (
        <EmptyState
          title="Nenhuma exposição no período"
          description="Não há série de trabalho registrada para este exercício no período selecionado."
        />
      ) : (
        <>
          <Box visibleFrom="md" mt="md">
            {/* 8 colunas depois do M35 (carga e RPE) — o minWidth acompanha
                para o cabeçalho não quebrar dentro da própria célula. */}
            <Table.ScrollContainer minWidth={1060}>
              <Table striped highlightOnHover withTableBorder verticalSpacing="xs">
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Data</Table.Th>
                    <Table.Th>Equipamento</Table.Th>
                    <Table.Th>Posição</Table.Th>
                    <Table.Th>Séries efetivas</Table.Th>
                    <Table.Th>Melhor carga</Table.Th>
                    {/* M35: o RPE por sessão fecha a paridade com a tela
                        legada `/exercicios` (carga, volume e RPE por sessão).
                        Só séries de trabalho. */}
                    <Table.Th>RPE médio</Table.Th>
                    <Table.Th>1RM est.</Table.Th>
                    <Table.Th>Tonelagem</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {linhas.map(({ exposure, mismatch }) => (
                    <Table.Tr
                      key={`${exposure.activity_id}-${exposure.position_in_session}`}
                      style={mismatch ? { opacity: 0.72 } : undefined}
                    >
                      <Table.Td>
                        <Stack gap={2}>
                          <Text size="sm">{fmtDate(exposure.local_date)}</Text>
                          <MismatchBadge mismatch={mismatch} />
                        </Stack>
                      </Table.Td>
                      <Table.Td>
                        <Stack gap={2}>
                          <Text size="sm">{exposure.equipment_name || EM_DASH}</Text>
                          <Text size="xs" c="dimmed">
                            {loadConventionLabel(exposure.load_convention)}
                          </Text>
                        </Stack>
                      </Table.Td>
                      <Table.Td>
                        <Stack gap={2}>
                          <Text size="sm">{fmtInt(exposure.position_in_session)}</Text>
                          {fatigueContextLabel(exposure.fatigue_context) && (
                            <Text size="xs" c="dimmed">
                              {fatigueContextLabel(exposure.fatigue_context)}
                            </Text>
                          )}
                        </Stack>
                      </Table.Td>
                      <Table.Td>
                        <Text size="xs" c={BRAND.textMuted}>
                          {setsText(exposure)}
                        </Text>
                      </Table.Td>
                      <Table.Td>{fmtKg(exposure.best_load_kg)}</Table.Td>
                      <Table.Td>{fmtRpe(exposure.avg_rpe)}</Table.Td>
                      <Table.Td>{fmtKg(exposure.e1rm)}</Table.Td>
                      <Table.Td>{fmtKg(exposure.tonnage)}</Table.Td>
                    </Table.Tr>
                  ))}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
          </Box>

          <Stack hiddenFrom="md" gap="sm" mt="md">
            {linhas.map(({ exposure, mismatch }) => (
              <Paper
                key={`${exposure.activity_id}-${exposure.position_in_session}`}
                withBorder
                radius="md"
                p="sm"
                style={{ background: BRAND.bgSurface, borderColor: BRAND.border, opacity: mismatch ? 0.78 : 1 }}
              >
                <Group justify="space-between" wrap="wrap" gap={4}>
                  <Text size="sm" fw={600} c={BRAND.textPrimary}>
                    {fmtDate(exposure.local_date)}
                  </Text>
                  <Text size="xs" c="dimmed">
                    {exposure.position_in_session != null ? `${exposure.position_in_session}º na sessão` : ''}
                    {fatigueContextLabel(exposure.fatigue_context)
                      ? ` · ${fatigueContextLabel(exposure.fatigue_context)}`
                      : ''}
                  </Text>
                </Group>
                <Text size="xs" c={BRAND.textMuted}>
                  {exposure.equipment_name || 'Equipamento não identificado'} ·{' '}
                  {loadConventionLabel(exposure.load_convention)}
                </Text>
                <MismatchBadge mismatch={mismatch} />
                <Divider my={6} color={BRAND.border} />
                <Text size="xs" c={BRAND.textMuted}>
                  {setsText(exposure)}
                </Text>
                <Group gap="md" mt={6} wrap="wrap">
                  <Text size="xs" c={BRAND.textPrimary}>
                    Carga: {fmtKg(exposure.best_load_kg)}
                  </Text>
                  <Text size="xs" c={BRAND.textPrimary}>
                    RPE: {fmtRpe(exposure.avg_rpe)}
                  </Text>
                  <Text size="xs" c={BRAND.textPrimary}>
                    1RM est.: {fmtKg(exposure.e1rm)}
                  </Text>
                  <Text size="xs" c={BRAND.textPrimary}>
                    Tonelagem: {fmtKg(exposure.tonnage)}
                  </Text>
                </Group>
              </Paper>
            ))}
          </Stack>
        </>
      )}
    </Paper>
  );
}

// ---------------------------------------------------------------------------
// Decisões do motor — EVIDÊNCIA secundária, nunca a regra
// ---------------------------------------------------------------------------

function DecisionsCard({ decisions }: { decisions: ExerciseProgressResponse['progression_decisions'] }) {
  return (
    <Paper withBorder radius="lg" p="md" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
      <Group gap="xs">
        <Text fw={700} c={BRAND.textPrimary}>
          Decisões do motor de progressão
        </Text>
        <Badge color="gray" variant="outline" radius="sm" size="sm">
          Evidência secundária
        </Badge>
      </Group>
      <Text size="sm" c={BRAND.textMuted}>
        O que o motor determinístico decidiu ao fechar cada sessão. Informa o contexto — o status acima continua
        vindo da regra de comparabilidade, não daqui.
      </Text>
      <Stack gap={6} mt="md">
        {decisions.map((decision) => (
          <Group key={decision.id} gap="xs" wrap="wrap">
            <Text size="sm" c={BRAND.textPrimary}>
              {fmtDate(decision.created_at)}
            </Text>
            <Badge variant="light" color="gray" radius="sm" size="sm">
              {progressionDecisionLabel(decision.decision)}
            </Badge>
            {decision.phase_before && decision.phase_after && (
              <Text size="xs" c={BRAND.textMuted}>
                fase {decision.phase_before} → {decision.phase_after}
              </Text>
            )}
            <Text size="xs" c="dimmed">
              {decision.engine_version}
            </Text>
          </Group>
        ))}
      </Stack>
    </Paper>
  );
}
