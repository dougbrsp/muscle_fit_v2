'use client';

import dynamic from 'next/dynamic';
import { ChartPlaceholder } from '@/components/charts/ChartPlaceholder';
import {
  ActionIcon,
  Alert,
  Badge,
  Group,
  Loader,
  Paper,
  SegmentedControl,
  Select,
  SimpleGrid,
  Stack,
  Table,
  Tabs,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { IconAlertTriangle, IconArrowDown, IconArrowRight, IconArrowUp, IconChevronDown, IconChevronUp } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { apiFetch, ApiError } from '@/lib/api';
import { browserStorage } from '@/lib/navbar-collapse';
import {
  DEFAULT_VOLUME_GROUPING_LEVEL,
  VOLUME_GROUPING_LABELS,
  VOLUME_GROUPING_OPTIONS,
  isVolumeGroupingLevel,
  readVolumeGroupingLevel,
  writeVolumeGroupingLevel,
  type VolumeGroupingLevel,
} from '@/lib/volume-grouping';
import type {
  ApiTrendDirection,
  HeatmapViewMode,
  RecentExerciseVolumeResponse,
  SlotComparativeSetsRow,
  SlotComparativeVolumeRow,
  SlotComparisonResponse,
  TrainingVolumeGranularity,
  TrainingVolumeResponse,
  WorkoutPeriodKey,
} from '@/lib/training-volume-types';
import { slotComparisonCaption, slotComparisonPath, slotSetsRows, slotVolumeRows } from '@/lib/slot-comparison';
import { TrainingGoalBadge } from '@/components/TrainingGoalBadge';
import { ALL_GOALS, goalQueryParam, sessionGoalFilterOptions } from '@/lib/training-goal-filter';
import { useTrainingGoals } from '@/lib/training-goals';
import {
  recentExerciseVolumePath,
  trainingVolumePath,
  volumeGrupamentosKeys,
  type RecentVolumeInclude,
  type RecentVolumeRange,
} from '@/lib/volume-grupamentos-queries';
import { GRANULARITY_WINDOW_LABELS, equipmentDisplayRows, sparklineSeries, trendBadge } from '@/lib/volume-grupamentos-view';
import { usePresentationSlot } from '@/lib/presentation-slots';
import { BRAND } from '@/theme';
import { formatFixed, formatKgFixed, formatSetsCount } from '@/lib/format';

// COD-14: recharts sob demanda; o placeholder tem a altura do gráfico.
const VolumeByGroupChart = dynamic(() => import('./VolumeGrupamentosCharts').then((m) => m.VolumeByGroupChart), {
  ssr: false,
  loading: () => <ChartPlaceholder height={280} />,
});
const TotalVolumeTrendChart = dynamic(() => import('./VolumeGrupamentosCharts').then((m) => m.TotalVolumeTrendChart), {
  ssr: false,
  loading: () => <ChartPlaceholder height={280} />,
});
const RepsChart = dynamic(() => import('./VolumeGrupamentosCharts').then((m) => m.RepsChart), {
  ssr: false,
  loading: () => <ChartPlaceholder height={320} />,
});
const HeatmapTreemap = dynamic(() => import('./VolumeGrupamentosCharts').then((m) => m.HeatmapTreemap), {
  ssr: false,
  loading: () => <ChartPlaceholder height={360} />,
});
// Sparkline de tamanho fixo (100x32): o placeholder ocupa o mesmo espaço.
const SparklineChart = dynamic(() => import('./VolumeGrupamentosCharts').then((m) => m.SparklineChart), {
  ssr: false,
  loading: () => <div aria-hidden style={{ width: 100, height: 32 }} />,
});

// Mesmo limite histórico já usado pela versão legada da tela (400 séries de
// exercícios mais recentes) — ver docstring de
// `VolumeService.get_recent_exercise_volume` no backend.
const RECENT_VOLUME_LIMIT = 400;

const granularityOptions = [
  { value: 'd7', label: 'D-7' },
  { value: 'week', label: 'Semana x Semana' },
  { value: 'month', label: 'Mês x Mês' },
];

// Mesmo mapeamento granularidade -> janela de dias buscada da referência
// on-premise (fitness-frontend/.../TrainingVolumeClient.tsx).
const queryDaysByGranularity: Record<TrainingVolumeGranularity, number> = {
  d7: 14,
  week: 21,
  month: 70,
};

const heatmapViewModeOptions: Array<{ value: HeatmapViewMode; label: string }> = [
  { value: 'session', label: 'Dia (sessão)' },
  { value: 'week', label: 'Semana (D-7)' },
  { value: 'month', label: 'Mês' },
];

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

// Formatos desta tela passados como `formatter` ao TrendValue (COD-09: a
// definição mora em lib/format.ts): volume em kg sem casa, RPE com 1 casa fixa.
const formatKgNoDecimals = (value: number | null) => formatKgFixed(value, 0);
const formatRpeOneDecimal = (value: number | null) => formatFixed(value, 1);

function formatSessionDate(localDate: string | undefined): string {
  if (!localDate) return '-';
  return new Date(`${localDate}T12:00:00`).toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit', year: 'numeric' });
}

// Utilitários de data só para os parâmetros do mapa de calor (intervalo escolhido na tela).
function parseDateKey(value: string): Date {
  return new Date(`${value}T12:00:00`);
}

function addDays(value: Date, days: number): Date {
  const next = new Date(value);
  next.setDate(next.getDate() + days);
  return new Date(next.getFullYear(), next.getMonth(), next.getDate());
}

function toDateKey(value: Date): string {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, '0');
  const day = String(value.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function todayDateKey(): string {
  return toDateKey(new Date());
}

function monthKeyOf(dateKey: string): string {
  return dateKey.slice(0, 7);
}

function lastDayOfMonthKey(monthKey: string): string {
  const [year, month] = monthKey.split('-').map(Number);
  const lastDay = new Date(year, month, 0).getDate();
  return `${monthKey}-${String(lastDay).padStart(2, '0')}`;
}

// Setas + cores de tendência (verde/vermelho semânticos padrão do Mantine;
// estável usa a cor de acento da marca, laranja).
/** Só exibe: a direção vem pronta do backend; `null`/ausente = sem seta. */
function TrendValue({
  value,
  direction,
  formatter,
}: {
  value: number | null;
  direction: ApiTrendDirection | undefined;
  formatter: (value: number | null) => string;
}) {
  const label = formatter(value);

  if (direction == null) {
    return <Text c={BRAND.textMuted}>{label}</Text>;
  }

  if (direction === 'up') {
    return (
      <Group gap={4} wrap="nowrap">
        <IconArrowUp size={14} color="var(--mantine-color-green-6)" />
        <Text c="green.6" fw={700}>{label}</Text>
      </Group>
    );
  }

  if (direction === 'down') {
    return (
      <Group gap={4} wrap="nowrap">
        <IconArrowDown size={14} color="var(--mantine-color-red-6)" />
        <Text c="red.6" fw={700}>{label}</Text>
      </Group>
    );
  }

  return (
    <Group gap={4} wrap="nowrap">
      <IconArrowRight size={14} color={BRAND.orange} />
      <Text c={BRAND.orange} fw={700}>{label}</Text>
    </Group>
  );
}

// Bloco 6: sparkline por linha nos comparativos (série t3..t0 pronta).
// Slot comparado (rótulo só para exibir; a chave é o ID) e quantas colunas o
// slot preencheu — as vazias nunca são completadas com outro slot.
function SlotCaption({
  row,
  sessionsPerSlot,
}: {
  row: SlotComparativeVolumeRow | SlotComparativeSetsRow;
  sessionsPerSlot: number;
}) {
  const filled = Object.values(row.byPeriod).filter((period) => period != null).length;
  if (row.comparisonStatus === 'no_slot') {
    return (
      <Badge size="xs" variant="light" color="gray" mt={4}>
        {slotComparisonCaption(row)}
      </Badge>
    );
  }
  return (
    <Text size="xs" c={BRAND.textMuted}>
      {slotComparisonCaption(row)}
      {filled < sessionsPerSlot && ` · ${filled} de ${sessionsPerSlot} treinos no slot`}
    </Text>
  );
}

function Sparkline({ values, color = BRAND.orange }: { values: Array<number | null>; color?: string }) {
  const hasData = values.some((value) => value != null && value > 0);

  if (!hasData) {
    return <Text size="xs" c={BRAND.textMuted}>sem dados</Text>;
  }

  return <SparklineChart values={values} color={color} />;
}

// Blocos semanal e por equipamento chegam prontos na mesma leitura (sem datas).
const RECENT_VOLUME_INCLUDE: RecentVolumeInclude[] = ['weekly', 'equipment_weekly'];

export function VolumeGrupamentosClient() {
  const PeriodList = usePresentationSlot('periodComparison');
  const RecordList = usePresentationSlot('recordList');
  const PageHeader = usePresentationSlot('pageHeader');
  const [granularity, setGranularity] = useState<TrainingVolumeGranularity>('d7');
  const [expandedEquipmentGroups, setExpandedEquipmentGroups] = useState<Set<string>>(new Set());
  const [heatmapViewMode, setHeatmapViewMode] = useState<HeatmapViewMode>('session');
  const [heatmapSessionKey, setHeatmapSessionKey] = useState<string | null>(null);
  const [heatmapRefDate, setHeatmapRefDate] = useState<string | null>(null);
  const [heatmapMonth, setHeatmapMonth] = useState<string | null>(null);
  // "Agrupar por": o primeiro render (inclusive o do export estático) usa o
  // padrão; a escolha salva no navegador entra depois da montagem.
  const [groupingLevel, setGroupingLevel] = useState<VolumeGroupingLevel>(DEFAULT_VOLUME_GROUPING_LEVEL);

  useEffect(() => {
    setGroupingLevel(readVolumeGroupingLevel(browserStorage()));
  }, []);

  const changeGroupingLevel = (value: string) => {
    if (!isVolumeGroupingLevel(value)) return;
    setGroupingLevel(value);
    setExpandedEquipmentGroups(new Set());
    writeVolumeGroupingLevel(browserStorage(), value);
  };

  // M97 F4: filtro de objetivo (rótulos de `GET /training-goals`). "Todos" =
  // sem `goal`, a tela de sempre; um objetivo filtra as leituras de volume por
  // exercício e o comparativo por slot (a leitura de repetições não filtra).
  const goals = useTrainingGoals().data;
  const [goalFilter, setGoalFilter] = useState<string>(ALL_GOALS);
  const goal = goalQueryParam(goals, goalFilter);

  const groupingLabel = VOLUME_GROUPING_LABELS[groupingLevel];
  const groupingLabelLower = groupingLabel.toLowerCase();

  const toggleEquipmentGroup = (groupKey: string) => {
    setExpandedEquipmentGroups((prev) => {
      const next = new Set(prev);
      if (next.has(groupKey)) next.delete(groupKey);
      else next.add(groupKey);
      return next;
    });
  };

  const recentVolumeQuery = useQuery({
    queryKey: volumeGrupamentosKeys.recentVolume(RECENT_VOLUME_LIMIT, goal, RECENT_VOLUME_INCLUDE),
    queryFn: () =>
      apiFetch<RecentExerciseVolumeResponse>(
        recentExerciseVolumePath(RECENT_VOLUME_LIMIT, null, goal, { include: RECENT_VOLUME_INCLUDE }),
      ),
  });

  // Referência estável: os memos abaixo (agora também por nível) dependem dela.
  const rows = useMemo(() => recentVolumeQuery.data?.items || [], [recentVolumeQuery.data]);

  // Comparativo T-0..T-3 pelo MESMO slot do Modelo (volume/RPE e séries
  // equivalentes), pronto no backend — GET /analytics/muscle-groups-slot-comparison
  // (dono: `intelligence/slot_session_comparison.py`). A tela não escolhe
  // sessões nem soma por nível: só lê o nível do seletor.
  const slotComparisonQuery = useQuery({
    queryKey: volumeGrupamentosKeys.slotComparison(RECENT_VOLUME_LIMIT, goal),
    queryFn: () =>
      apiFetch<SlotComparisonResponse>(slotComparisonPath(RECENT_VOLUME_LIMIT, goal)),
  });
  // Quantas colunas o slot pode preencher vem da API (`SLOT_COMPARISON_SESSIONS`); sem resposta não há linhas.
  const sessionsPerSlot = slotComparisonQuery.data?.sessions_per_slot ?? 0;

  const comparativeSetsRows = useMemo<SlotComparativeSetsRow[]>(
    () => slotSetsRows(slotComparisonQuery.data, groupingLevel),
    [slotComparisonQuery.data, groupingLevel],
  );

  // Blocos 2 e 3: comparativos semanal e por equipamento (S-3..S-0) prontos do
  // backend no nível escolhido; API antiga (sem os blocos) = estado vazio.
  const comparativeWeekRows = recentVolumeQuery.data?.weekly?.levels?.[groupingLevel] ?? [];
  const equipmentComparativeRows = useMemo(
    () => equipmentDisplayRows(recentVolumeQuery.data?.equipment_weekly?.levels?.[groupingLevel]),
    [recentVolumeQuery.data, groupingLevel],
  );

  // Bloco 4: gráfico de repetições + "Variação por grupamento". Com
  // `granularity`, o backend devolve `period_comparison` (janelas, deltas,
  // direção e ordem prontos); API antiga = estado vazio.
  const queryDays = queryDaysByGranularity[granularity];
  const trainingVolumeQuery = useQuery({
    queryKey: volumeGrupamentosKeys.trainingVolume(queryDays, granularity),
    queryFn: () => apiFetch<TrainingVolumeResponse>(trainingVolumePath(queryDays, granularity)),
  });

  const windowLabels = GRANULARITY_WINDOW_LABELS[granularity];
  const periodComparison = trainingVolumeQuery.data?.period_comparison ?? null;
  const repsComparison = useMemo(
    () => periodComparison?.levels?.[groupingLevel] ?? [],
    [periodComparison, groupingLevel],
  );

  const repsChartData = useMemo(
    () => repsComparison.map((item) => ({ grupo: item.group.label, atual: item.current_reps, anterior: item.previous_reps })),
    [repsComparison],
  );

  // Bloco 5: mapa de calor — células prontas do backend (`include=heatmap`):
  // sessão por `activity_id`, semana/mês por `date_from`/`date_to`.
  const recentVolumeRowsSorted = useMemo(() => {
    if (!rows.length) return [];
    return [...rows].sort((left, right) => {
      if (left.local_date !== right.local_date) return right.local_date.localeCompare(left.local_date);
      return right.activity_id - left.activity_id;
    });
  }, [rows]);

  const heatmapSessionOptions = useMemo(() => {
    const byKey = new Map<string, { activity_id: number; local_date: string }>();
    for (const item of recentVolumeRowsSorted) {
      const key = `${item.local_date}|${item.activity_id}`;
      if (!byKey.has(key)) byKey.set(key, { activity_id: item.activity_id, local_date: item.local_date });
    }
    return Array.from(byKey.entries()).map(([key, session]) => ({ key, ...session }));
  }, [recentVolumeRowsSorted]);

  const selectedHeatmapSessionKey = heatmapSessionKey ?? heatmapSessionOptions[0]?.key ?? null;
  const latestAvailableDate = recentVolumeRowsSorted[0]?.local_date || null;

  const effectiveHeatmapRefDate = heatmapRefDate ?? latestAvailableDate ?? todayDateKey();
  const effectiveHeatmapMonth = heatmapMonth ?? (latestAvailableDate ? monthKeyOf(latestAvailableDate) : monthKeyOf(todayDateKey()));

  // Parâmetro de consulta: intervalo (7 dias até a data / mês) escolhido pelo usuário.
  const heatmapRange = useMemo<RecentVolumeRange | null>(() => {
    if (heatmapViewMode === 'week') {
      return { dateFrom: toDateKey(addDays(parseDateKey(effectiveHeatmapRefDate), -6)), dateTo: effectiveHeatmapRefDate };
    }
    if (heatmapViewMode === 'month') {
      return { dateFrom: `${effectiveHeatmapMonth}-01`, dateTo: lastDayOfMonthKey(effectiveHeatmapMonth) };
    }
    return null;
  }, [heatmapViewMode, effectiveHeatmapRefDate, effectiveHeatmapMonth]);
  const heatmapActivityId =
    heatmapViewMode === 'session' && selectedHeatmapSessionKey ? Number(selectedHeatmapSessionKey.split('|')[1]) : null;
  const heatmapEnabled = heatmapRange != null || heatmapActivityId != null;

  const heatmapQuery = useQuery({
    queryKey: volumeGrupamentosKeys.heatmap(heatmapRange, heatmapActivityId, goal),
    queryFn: () =>
      apiFetch<RecentExerciseVolumeResponse>(
        recentExerciseVolumePath(RECENT_VOLUME_LIMIT, heatmapRange, goal, { include: ['heatmap'], activityId: heatmapActivityId }),
      ),
    enabled: heatmapEnabled,
  });

  const heatmapIsLoading = recentVolumeQuery.isLoading || (heatmapEnabled && heatmapQuery.isLoading);
  const heatmapIsError = heatmapQuery.isError || (heatmapViewMode === 'session' && recentVolumeQuery.isError);
  const heatmapCells = useMemo(
    () => (heatmapEnabled ? heatmapQuery.data?.heatmap?.levels?.[groupingLevel]?.cells ?? [] : []),
    [heatmapEnabled, heatmapQuery.data, groupingLevel],
  );

  const heatmapTreemapData = useMemo(
    () => heatmapCells.map((cell) => ({
      name: cell.group.label,
      sets_count: cell.sets_count,
      avg_weight_kg: cell.avg_weight_kg,
      volume_kg: cell.volume_kg,
      volume_ratio: cell.volume_ratio,
    })),
    [heatmapCells],
  );

  // Comparativo T-0..T-3 no nível escolhido (tabela e gráfico "T-0 vs T-1").
  const comparativeRows = useMemo<SlotComparativeVolumeRow[]>(
    () => slotVolumeRows(slotComparisonQuery.data, groupingLevel),
    [slotComparisonQuery.data, groupingLevel],
  );
  // Cards de resumo e "Volume total" seguem por GRUPO em qualquer nível e chegam
  // prontos do backend (`levels.muscle_group.totals`); API antiga (sem `totals`)
  // deixa os cards em "—" e o gráfico vazio — a tela não soma.
  const slotTotals = slotComparisonQuery.data?.levels?.muscle_group?.totals ?? null;

  // Top 10 por volume T-0 do nível escolhido, já ordenado e cortado pelo backend.
  const volumeByGroupChartData = useMemo(
    () =>
      (slotComparisonQuery.data?.levels?.[groupingLevel]?.top_volume_t0 ?? []).map((row) => ({
        grupo: row.group.label,
        t0: row.t0,
        t1: row.t1 ?? 0,
      })),
    [slotComparisonQuery.data, groupingLevel],
  );

  // Volume total por período T-3..T-0 (soma de todos os grupos feita no backend).
  const totalVolumeTrendChartData = useMemo(() => {
    if (!slotTotals) return [];
    const periods: WorkoutPeriodKey[] = ['t3', 't2', 't1', 't0'];
    const labels: Record<WorkoutPeriodKey, string> = { t3: 'T-3', t2: 'T-2', t1: 'T-1', t0: 'T-0' };
    return periods.map((period) => ({
      periodo: labels[period],
      volume: slotTotals.by_period[period] ?? 0,
    }));
  }, [slotTotals]);

  return (
    <Stack gap="xl">
      <Group justify="space-between" align="flex-end">
        {PageHeader ? (
          <PageHeader
            title="Volume por grupamento"
            description="Comparativo de volume e RPE médio nas últimas 4 sessões (T-3 a T-0), com indicação de tendência."
          />
        ) : (
        <div>
          <Title order={1} c={BRAND.textPrimary}>
            Volume por grupamento
          </Title>
          <Text c={BRAND.textMuted} size="md">
            Comparativo de volume e RPE médio nas últimas 4 sessões (T-3 a T-0), com indicação de tendência.
          </Text>
        </div>
        )}
        <Group gap="md" align="flex-end" wrap="wrap">
          {goals && (
            <Stack gap={4}>
              <Group gap={6}>
                <Text size="sm" fw={500} c={BRAND.textPrimary} id="volume-objetivo">
                  Objetivo
                </Text>
                {goal && <TrainingGoalBadge value={goal} size="xs" />}
              </Group>
              <SegmentedControl
                aria-labelledby="volume-objetivo"
                value={goal ?? ALL_GOALS}
                onChange={setGoalFilter}
                data={sessionGoalFilterOptions(goals)}
              />
            </Stack>
          )}
          <Stack gap={4}>
            <Text size="sm" fw={500} c={BRAND.textPrimary} id="volume-agrupar-por">
              Agrupar por
            </Text>
            <SegmentedControl
              aria-labelledby="volume-agrupar-por"
              value={groupingLevel}
              onChange={changeGroupingLevel}
              data={VOLUME_GROUPING_OPTIONS}
            />
          </Stack>
        </Group>
      </Group>

      {recentVolumeQuery.isError && (
        <Alert color="red" icon={<IconAlertTriangle />} title="Erro ao carregar volume por grupamento">
          {errorMessage(recentVolumeQuery.error, 'Não foi possível consultar a Fitness API.')}
        </Alert>
      )}

      {recentVolumeQuery.isLoading && (
        <Group>
          <Loader size="sm" color="brand" />
          <Text c={BRAND.textMuted}>Carregando dados da Fitness API...</Text>
        </Group>
      )}

      <SimpleGrid cols={{ base: 1, sm: 3 }}>
        <Paper withBorder radius="lg" p="md" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
          <Text size="xs" c={BRAND.textMuted} tt="uppercase" fw={600}>
            Grupos treinados (T-0)
          </Text>
          <Title order={2} c={BRAND.textPrimary}>
            {slotTotals ? `${slotTotals.groups_with_t0} / ${slotTotals.groups_tracked}` : '—'}
          </Title>
        </Paper>
        <Paper withBorder radius="lg" p="md" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
          <Text size="xs" c={BRAND.textMuted} tt="uppercase" fw={600}>
            Volume na última sessão (T-0)
          </Text>
          <Title order={2} c={BRAND.textPrimary}>
            {slotTotals ? formatKgFixed(slotTotals.by_period.t0 ?? 0, 0) : '—'}
          </Title>
        </Paper>
        <Paper withBorder radius="lg" p="md" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
          <Text size="xs" c={BRAND.textMuted} tt="uppercase" fw={600}>
            RPE médio (T-0)
          </Text>
          <Title order={2} c={BRAND.textPrimary}>
            {slotTotals?.avg_rpe_t0 != null ? formatFixed(slotTotals.avg_rpe_t0, 1) : '-'}
          </Title>
        </Paper>
      </SimpleGrid>

      <SimpleGrid cols={{ base: 1, lg: 2 }}>
        <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
          <Title order={3} mb="xs" c={BRAND.textPrimary}>
            Volume por {groupingLabelLower}: T-0 vs T-1
          </Title>
          <Text c={BRAND.textMuted} size="sm" mb="sm">
            Última sessão comparada à anterior no mesmo slot do Modelo, por {groupingLabelLower} (top 10 por volume
            em T-0). Sem treino anterior no slot, T-1 fica zerado.
          </Text>
          {volumeByGroupChartData.length > 0 ? (
            <VolumeByGroupChart volumeByGroupChartData={volumeByGroupChartData} />
          ) : (
            <Text c={BRAND.textMuted}>Sem dados suficientes para o comparativo.</Text>
          )}
        </Paper>

        <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
          <Title order={3} mb="xs" c={BRAND.textPrimary}>
            Volume total: últimas 4 sessões
          </Title>
          <Text c={BRAND.textMuted} size="sm" mb="sm">
            Soma do volume de todos os grupos musculares, por posição no histórico do mesmo slot de cada grupo (T-3
            mais antiga, T-0 mais recente).
          </Text>
          <TotalVolumeTrendChart totalVolumeTrendChartData={totalVolumeTrendChartData} />
        </Paper>
      </SimpleGrid>

      <Tabs defaultValue="comparativos" color="brand">
        <Tabs.List>
          <Tabs.Tab value="comparativos">Comparativos</Tabs.Tab>
          <Tabs.Tab value="tendencia">Tendência</Tabs.Tab>
          <Tabs.Tab value="mapa-calor">Mapa de calor</Tabs.Tab>
        </Tabs.List>

        <Tabs.Panel value="comparativos" pt="lg">
      <Stack gap="xl">
      <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
        <Group justify="space-between" mb="xs">
          <Title order={3} c={BRAND.textPrimary}>
            Comparativo das últimas 4 sessões por {groupingLabelLower}
          </Title>
        </Group>
        <Text c={BRAND.textMuted} size="sm" mb="sm">
          T-0 = última sessão do {groupingLabelLower}; T-1 a T-3 = as sessões anteriores no MESMO slot do Modelo
          (identificado pelo ID, não pelo nome). “-” = sem treino anterior nesse slot; nunca é completado com outro
          slot. Setas: verde subiu, vermelho caiu, laranja estável.
        </Text>
        {PeriodList ? (
          <PeriodList
            groupingLabel={groupingLabel}
            periodLabels={['T-3', 'T-2', 'T-1', 'T-0']}
            metricLabels={['Volume', 'RPE médio']}
            emptyText={comparativeRows.length === 0 && !slotComparisonQuery.isLoading ? 'Sem dados de treino recentes para montar o comparativo.' : ''}
            rows={comparativeRows.map((row) => {
              const t3 = row.byPeriod.t3;
              const t2 = row.byPeriod.t2;
              const t1 = row.byPeriod.t1;
              const t0 = row.byPeriod.t0;
              return {
                key: row.group.key,
                label: <Text fw={600} c={BRAND.textPrimary}>{row.group.label}</Text>,
                caption: (
                  <>
                    {t0 && (
                      <Text size="xs" c={BRAND.textMuted}>
                        Última sessão: {formatSessionDate(t0.local_date)}
                      </Text>
                    )}
                    <SlotCaption row={row} sessionsPerSlot={sessionsPerSlot} />
                  </>
                ),
                periods: [
                  [formatKgFixed(t3?.volume_kg ?? null, 0), formatFixed(t3?.avg_rpe ?? null, 1)],
                  [
                    <TrendValue key="v" value={t2?.volume_kg ?? null} direction={t2?.volume_direction} formatter={formatKgNoDecimals} />,
                    <TrendValue key="r" value={t2?.avg_rpe ?? null} direction={t2?.rpe_direction} formatter={formatRpeOneDecimal} />,
                  ],
                  [
                    <TrendValue key="v" value={t1?.volume_kg ?? null} direction={t1?.volume_direction} formatter={formatKgNoDecimals} />,
                    <TrendValue key="r" value={t1?.avg_rpe ?? null} direction={t1?.rpe_direction} formatter={formatRpeOneDecimal} />,
                  ],
                  [
                    <TrendValue key="v" value={t0?.volume_kg ?? null} direction={t0?.volume_direction} formatter={formatKgNoDecimals} />,
                    <TrendValue key="r" value={t0?.avg_rpe ?? null} direction={t0?.rpe_direction} formatter={formatRpeOneDecimal} />,
                  ],
                ],
                evolution: <Sparkline values={sparklineSeries(row.byPeriod, (p) => p.volume_kg)} />,
              };
            })}
          />
        ) : (
        <Table.ScrollContainer minWidth={900}>
          <Table striped highlightOnHover withTableBorder verticalSpacing="sm">
            <Table.Thead>
              <Table.Tr>
                <Table.Th rowSpan={2}>{groupingLabel}</Table.Th>
                <Table.Th colSpan={2}>T-3</Table.Th>
                <Table.Th colSpan={2}>T-2</Table.Th>
                <Table.Th colSpan={2}>T-1</Table.Th>
                <Table.Th colSpan={2}>T-0</Table.Th>
                <Table.Th rowSpan={2}>Evolução</Table.Th>
              </Table.Tr>
              <Table.Tr>
                <Table.Th>Volume</Table.Th>
                <Table.Th>RPE médio</Table.Th>
                <Table.Th>Volume</Table.Th>
                <Table.Th>RPE médio</Table.Th>
                <Table.Th>Volume</Table.Th>
                <Table.Th>RPE médio</Table.Th>
                <Table.Th>Volume</Table.Th>
                <Table.Th>RPE médio</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {comparativeRows.map((row) => {
                const t3 = row.byPeriod.t3;
                const t2 = row.byPeriod.t2;
                const t1 = row.byPeriod.t1;
                const t0 = row.byPeriod.t0;

                return (
                  <Table.Tr key={row.group.key}>
                    <Table.Td>
                      <Text fw={600} c={BRAND.textPrimary}>{row.group.label}</Text>
                      {t0 && (
                        <Text size="xs" c={BRAND.textMuted}>
                          Última sessão: {formatSessionDate(t0.local_date)}
                        </Text>
                      )}
                      <SlotCaption row={row} sessionsPerSlot={sessionsPerSlot} />
                    </Table.Td>

                    <Table.Td>{formatKgFixed(t3?.volume_kg ?? null, 0)}</Table.Td>
                    <Table.Td>{formatFixed(t3?.avg_rpe ?? null, 1)}</Table.Td>

                    <Table.Td>
                      <TrendValue value={t2?.volume_kg ?? null} direction={t2?.volume_direction} formatter={formatKgNoDecimals} />
                    </Table.Td>
                    <Table.Td>
                      <TrendValue value={t2?.avg_rpe ?? null} direction={t2?.rpe_direction} formatter={formatRpeOneDecimal} />
                    </Table.Td>

                    <Table.Td>
                      <TrendValue value={t1?.volume_kg ?? null} direction={t1?.volume_direction} formatter={formatKgNoDecimals} />
                    </Table.Td>
                    <Table.Td>
                      <TrendValue value={t1?.avg_rpe ?? null} direction={t1?.rpe_direction} formatter={formatRpeOneDecimal} />
                    </Table.Td>

                    <Table.Td>
                      <TrendValue value={t0?.volume_kg ?? null} direction={t0?.volume_direction} formatter={formatKgNoDecimals} />
                    </Table.Td>
                    <Table.Td>
                      <TrendValue value={t0?.avg_rpe ?? null} direction={t0?.rpe_direction} formatter={formatRpeOneDecimal} />
                    </Table.Td>
                    <Table.Td><Sparkline values={sparklineSeries(row.byPeriod, (p) => p.volume_kg)} /></Table.Td>
                  </Table.Tr>
                );
              })}
              {comparativeRows.length === 0 && !slotComparisonQuery.isLoading && (
                <Table.Tr>
                  <Table.Td colSpan={10}>
                    <Text c={BRAND.textMuted} ta="center">
                      Sem dados de treino recentes para montar o comparativo.
                    </Text>
                  </Table.Td>
                </Table.Tr>
              )}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
        )}
      </Paper>

      <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
        <Group justify="space-between" mb="xs">
          <Title order={3} c={BRAND.textPrimary}>
            Séries equivalentes executadas — últimos 4 treinos
          </Title>
        </Group>
        <Text c={BRAND.textMuted} size="sm" mb="sm">
          PRIMARY conta peso 1.0, SECONDARY/STABILIZER usam o stimulus_weight real, diferente da contagem bruta de
          séries. T-1 a T-3 = treinos anteriores no mesmo slot do Modelo do T-0; “-” = sem treino nesse slot.
          {groupingLevel !== 'muscle_group' &&
            ' Por grupo pai ou macro-grupo, é a soma dos grupos: o exercício que trabalha a fatia e o pai conta nos dois.'}
        </Text>
        {slotComparisonQuery.isError && (
          <Alert color="red" icon={<IconAlertTriangle />} mb="sm">
            {errorMessage(slotComparisonQuery.error, 'Não foi possível carregar o comparativo por slot.')}
          </Alert>
        )}
        {PeriodList ? (
          <PeriodList
            groupingLabel={groupingLabel}
            periodLabels={['T-3', 'T-2', 'T-1', 'T-0']}
            metricLabels={['Séries equivalentes']}
            emptyText={comparativeSetsRows.length === 0 && !slotComparisonQuery.isLoading ? 'Sem dados suficientes para o comparativo.' : ''}
            rows={comparativeSetsRows.map((row) => {
              const t3 = row.byPeriod.t3;
              const t2 = row.byPeriod.t2;
              const t1 = row.byPeriod.t1;
              const t0 = row.byPeriod.t0;
              return {
                key: row.group.key,
                label: <Text fw={600} c={BRAND.textPrimary}>{row.group.label}</Text>,
                caption: <SlotCaption row={row} sessionsPerSlot={sessionsPerSlot} />,
                periods: [
                  [formatSetsCount(t3?.sets_count ?? null)],
                  [<TrendValue key="s" value={t2?.sets_count ?? null} direction={t2?.sets_direction} formatter={formatSetsCount} />],
                  [<TrendValue key="s" value={t1?.sets_count ?? null} direction={t1?.sets_direction} formatter={formatSetsCount} />],
                  [<TrendValue key="s" value={t0?.sets_count ?? null} direction={t0?.sets_direction} formatter={formatSetsCount} />],
                ],
                evolution: <Sparkline values={sparklineSeries(row.byPeriod, (p) => p.sets_count)} />,
              };
            })}
          />
        ) : (
        <Table.ScrollContainer minWidth={700}>
          <Table striped highlightOnHover withTableBorder verticalSpacing="sm">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>{groupingLabel}</Table.Th>
                <Table.Th>T-3</Table.Th>
                <Table.Th>T-2</Table.Th>
                <Table.Th>T-1</Table.Th>
                <Table.Th>T-0</Table.Th>
                <Table.Th>Evolução</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {comparativeSetsRows.map((row) => {
                const t3 = row.byPeriod.t3;
                const t2 = row.byPeriod.t2;
                const t1 = row.byPeriod.t1;
                const t0 = row.byPeriod.t0;

                return (
                  <Table.Tr key={row.group.key}>
                    <Table.Td>
                      <Text fw={600} c={BRAND.textPrimary}>{row.group.label}</Text>
                      <SlotCaption row={row} sessionsPerSlot={sessionsPerSlot} />
                    </Table.Td>
                    <Table.Td>{formatSetsCount(t3?.sets_count ?? null)}</Table.Td>
                    <Table.Td><TrendValue value={t2?.sets_count ?? null} direction={t2?.sets_direction} formatter={formatSetsCount} /></Table.Td>
                    <Table.Td><TrendValue value={t1?.sets_count ?? null} direction={t1?.sets_direction} formatter={formatSetsCount} /></Table.Td>
                    <Table.Td><TrendValue value={t0?.sets_count ?? null} direction={t0?.sets_direction} formatter={formatSetsCount} /></Table.Td>
                    <Table.Td><Sparkline values={sparklineSeries(row.byPeriod, (p) => p.sets_count)} /></Table.Td>
                  </Table.Tr>
                );
              })}
              {comparativeSetsRows.length === 0 && !slotComparisonQuery.isLoading && (
                <Table.Tr>
                  <Table.Td colSpan={6}>
                    <Text c={BRAND.textMuted} ta="center">Sem dados suficientes para o comparativo.</Text>
                  </Table.Td>
                </Table.Tr>
              )}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
        )}
      </Paper>

      <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
        <Group justify="space-between" mb="xs">
          <Title order={3} c={BRAND.textPrimary}>
            Comparativo das últimas 4 semanas por {groupingLabelLower}
          </Title>
        </Group>
        <Text c={BRAND.textMuted} size="sm" mb="sm">
          Volume e RPE médio por semana (segunda-feira da semana), prontos do backend — sessões da mesma semana
          somam no mesmo ponto (S-3 mais antiga, S-0 atual).
        </Text>
        {PeriodList ? (
          <PeriodList
            groupingLabel={groupingLabel}
            periodLabels={['S-3', 'S-2', 'S-1', 'S-0']}
            metricLabels={['Volume', 'RPE médio']}
            emptyText={comparativeWeekRows.length === 0 && !recentVolumeQuery.isLoading ? 'Sem dados suficientes para o comparativo semanal.' : ''}
            rows={comparativeWeekRows.map((row) => {
              const s3 = row.by_period.t3;
              const s2 = row.by_period.t2;
              const s1 = row.by_period.t1;
              const s0 = row.by_period.t0;
              return {
                key: row.group.key,
                label: <Text fw={600} c={BRAND.textPrimary}>{row.group.label}</Text>,
                periods: [
                  [formatKgFixed(s3?.volume_kg ?? null, 0), formatFixed(s3?.avg_rpe ?? null, 1)],
                  [
                    <TrendValue key="v" value={s2?.volume_kg ?? null} direction={s2?.volume_direction} formatter={formatKgNoDecimals} />,
                    <TrendValue key="r" value={s2?.avg_rpe ?? null} direction={s2?.rpe_direction} formatter={formatRpeOneDecimal} />,
                  ],
                  [
                    <TrendValue key="v" value={s1?.volume_kg ?? null} direction={s1?.volume_direction} formatter={formatKgNoDecimals} />,
                    <TrendValue key="r" value={s1?.avg_rpe ?? null} direction={s1?.rpe_direction} formatter={formatRpeOneDecimal} />,
                  ],
                  [
                    <TrendValue key="v" value={s0?.volume_kg ?? null} direction={s0?.volume_direction} formatter={formatKgNoDecimals} />,
                    <TrendValue key="r" value={s0?.avg_rpe ?? null} direction={s0?.rpe_direction} formatter={formatRpeOneDecimal} />,
                  ],
                ],
                evolution: <Sparkline values={sparklineSeries(row.by_period, (p) => p.volume_kg)} color={BRAND.red} />,
              };
            })}
          />
        ) : (
        <Table.ScrollContainer minWidth={900}>
          <Table striped highlightOnHover withTableBorder verticalSpacing="sm">
            <Table.Thead>
              <Table.Tr>
                <Table.Th rowSpan={2}>{groupingLabel}</Table.Th>
                <Table.Th colSpan={2}>S-3</Table.Th>
                <Table.Th colSpan={2}>S-2</Table.Th>
                <Table.Th colSpan={2}>S-1</Table.Th>
                <Table.Th colSpan={2}>S-0</Table.Th>
                <Table.Th rowSpan={2}>Evolução</Table.Th>
              </Table.Tr>
              <Table.Tr>
                <Table.Th>Volume</Table.Th>
                <Table.Th>RPE médio</Table.Th>
                <Table.Th>Volume</Table.Th>
                <Table.Th>RPE médio</Table.Th>
                <Table.Th>Volume</Table.Th>
                <Table.Th>RPE médio</Table.Th>
                <Table.Th>Volume</Table.Th>
                <Table.Th>RPE médio</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {comparativeWeekRows.map((row) => {
                const s3 = row.by_period.t3;
                const s2 = row.by_period.t2;
                const s1 = row.by_period.t1;
                const s0 = row.by_period.t0;

                return (
                  <Table.Tr key={row.group.key}>
                    <Table.Td><Text fw={600} c={BRAND.textPrimary}>{row.group.label}</Text></Table.Td>

                    <Table.Td>{formatKgFixed(s3?.volume_kg ?? null, 0)}</Table.Td>
                    <Table.Td>{formatFixed(s3?.avg_rpe ?? null, 1)}</Table.Td>

                    <Table.Td><TrendValue value={s2?.volume_kg ?? null} direction={s2?.volume_direction} formatter={formatKgNoDecimals} /></Table.Td>
                    <Table.Td><TrendValue value={s2?.avg_rpe ?? null} direction={s2?.rpe_direction} formatter={formatRpeOneDecimal} /></Table.Td>

                    <Table.Td><TrendValue value={s1?.volume_kg ?? null} direction={s1?.volume_direction} formatter={formatKgNoDecimals} /></Table.Td>
                    <Table.Td><TrendValue value={s1?.avg_rpe ?? null} direction={s1?.rpe_direction} formatter={formatRpeOneDecimal} /></Table.Td>

                    <Table.Td><TrendValue value={s0?.volume_kg ?? null} direction={s0?.volume_direction} formatter={formatKgNoDecimals} /></Table.Td>
                    <Table.Td><TrendValue value={s0?.avg_rpe ?? null} direction={s0?.rpe_direction} formatter={formatRpeOneDecimal} /></Table.Td>
                    <Table.Td><Sparkline values={sparklineSeries(row.by_period, (p) => p.volume_kg)} color={BRAND.red} /></Table.Td>
                  </Table.Tr>
                );
              })}
              {comparativeWeekRows.length === 0 && !recentVolumeQuery.isLoading && (
                <Table.Tr>
                  <Table.Td colSpan={10}>
                    <Text c={BRAND.textMuted} ta="center">Sem dados suficientes para o comparativo semanal.</Text>
                  </Table.Td>
                </Table.Tr>
              )}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
        )}
      </Paper>

      <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
        <Group justify="space-between" mb="xs">
          <Title order={3} c={BRAND.textPrimary}>
            Comparativo por equipamento (últimas 4 semanas)
          </Title>
        </Group>
        <Text c={BRAND.textMuted} size="sm" mb="sm">
          Mesma fonte agregada por equipamento dentro de cada {groupingLabelLower} e semana — expanda o bloco para
          ver se a carga está concentrada num único equipamento.
        </Text>
        {PeriodList ? (
          <PeriodList
            groupingLabel={groupingLabel}
            periodLabels={['S-3', 'S-2', 'S-1', 'S-0']}
            metricLabels={['Séries', 'Peso na última série']}
            emptyText={equipmentComparativeRows.length === 0 && !recentVolumeQuery.isLoading ? 'Sem dados suficientes para o comparativo por equipamento.' : ''}
            rows={equipmentComparativeRows
              .filter((row) => row.isTotal || expandedEquipmentGroups.has(row.group.key))
              .map((row, index) => {
                const s3 = row.byPeriod.t3;
                const s2 = row.byPeriod.t2;
                const s1 = row.byPeriod.t1;
                const s0 = row.byPeriod.t0;
                const isExpanded = expandedEquipmentGroups.has(row.group.key);
                return {
                  key: `${row.group.key}-${row.label}-${index}`,
                  label: (
                    <>
                      <Text fw={600} c={BRAND.textPrimary}>{row.group.label}</Text>
                      <Text fw={row.isTotal ? 700 : 400} size="sm" c={row.isTotal ? BRAND.textPrimary : BRAND.textMuted}>
                        {row.label}
                      </Text>
                    </>
                  ),
                  periods: [
                    [formatSetsCount(s3?.sets_count ?? null), formatKgFixed(s3?.last_weight_kg ?? null, 0)],
                    [
                      <TrendValue key="s" value={s2?.sets_count ?? null} direction={s2?.sets_direction} formatter={formatSetsCount} />,
                      <TrendValue key="w" value={s2?.last_weight_kg ?? null} direction={s2?.last_weight_direction} formatter={formatKgNoDecimals} />,
                    ],
                    [
                      <TrendValue key="s" value={s1?.sets_count ?? null} direction={s1?.sets_direction} formatter={formatSetsCount} />,
                      <TrendValue key="w" value={s1?.last_weight_kg ?? null} direction={s1?.last_weight_direction} formatter={formatKgNoDecimals} />,
                    ],
                    [
                      <TrendValue key="s" value={s0?.sets_count ?? null} direction={s0?.sets_direction} formatter={formatSetsCount} />,
                      <TrendValue key="w" value={s0?.last_weight_kg ?? null} direction={s0?.last_weight_direction} formatter={formatKgNoDecimals} />,
                    ],
                  ],
                  evolution: <Sparkline values={sparklineSeries(row.byPeriod, (p) => p.volume_kg)} color="#8B93A6" />,
                  toggle:
                    row.isTotal && row.groupSize > 1
                      ? { expanded: isExpanded, onToggle: () => toggleEquipmentGroup(row.group.key) }
                      : undefined,
                };
              })}
          />
        ) : (
        <Table.ScrollContainer minWidth={950}>
          <Table striped highlightOnHover withTableBorder verticalSpacing="sm">
            <Table.Thead>
              <Table.Tr>
                <Table.Th rowSpan={2}>{groupingLabel}</Table.Th>
                <Table.Th rowSpan={2}>Equipamento</Table.Th>
                <Table.Th colSpan={2}>S-3</Table.Th>
                <Table.Th colSpan={2}>S-2</Table.Th>
                <Table.Th colSpan={2}>S-1</Table.Th>
                <Table.Th colSpan={2}>S-0</Table.Th>
                <Table.Th rowSpan={2}>Evolução (volume)</Table.Th>
              </Table.Tr>
              <Table.Tr>
                <Table.Th>Séries</Table.Th>
                <Table.Th>Peso na última série</Table.Th>
                <Table.Th>Séries</Table.Th>
                <Table.Th>Peso na última série</Table.Th>
                <Table.Th>Séries</Table.Th>
                <Table.Th>Peso na última série</Table.Th>
                <Table.Th>Séries</Table.Th>
                <Table.Th>Peso na última série</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {equipmentComparativeRows
                .filter((row) => row.isTotal || expandedEquipmentGroups.has(row.group.key))
                .map((row, index) => {
                  const s3 = row.byPeriod.t3;
                  const s2 = row.byPeriod.t2;
                  const s1 = row.byPeriod.t1;
                  const s0 = row.byPeriod.t0;
                  const isExpanded = expandedEquipmentGroups.has(row.group.key);
                  const rowSpan = isExpanded ? row.groupSize : 1;

                  return (
                    <Table.Tr key={`${row.group.key}-${row.label}-${index}`}>
                      {row.isFirstOfGroup && (
                        <Table.Td rowSpan={rowSpan} style={{ verticalAlign: 'top' }}>
                          <Text fw={600} c={BRAND.textPrimary}>{row.group.label}</Text>
                        </Table.Td>
                      )}
                      <Table.Td>
                        {row.isTotal ? (
                          <Group gap={4} wrap="nowrap">
                            <Text fw={700} size="sm" c={BRAND.textPrimary}>{row.label}</Text>
                            {row.groupSize > 1 && (
                              <ActionIcon
                                variant="subtle"
                                color="brand"
                                size="sm"
                                aria-label={isExpanded ? 'Recolher equipamentos' : 'Expandir equipamentos'}
                                onClick={() => toggleEquipmentGroup(row.group.key)}
                              >
                                {isExpanded ? <IconChevronUp size={16} /> : <IconChevronDown size={16} />}
                              </ActionIcon>
                            )}
                          </Group>
                        ) : (
                          <Text c={BRAND.textMuted}>{row.label}</Text>
                        )}
                      </Table.Td>

                      <Table.Td>{formatSetsCount(s3?.sets_count ?? null)}</Table.Td>
                      <Table.Td>{formatKgFixed(s3?.last_weight_kg ?? null, 0)}</Table.Td>

                      <Table.Td><TrendValue value={s2?.sets_count ?? null} direction={s2?.sets_direction} formatter={formatSetsCount} /></Table.Td>
                      <Table.Td><TrendValue value={s2?.last_weight_kg ?? null} direction={s2?.last_weight_direction} formatter={formatKgNoDecimals} /></Table.Td>

                      <Table.Td><TrendValue value={s1?.sets_count ?? null} direction={s1?.sets_direction} formatter={formatSetsCount} /></Table.Td>
                      <Table.Td><TrendValue value={s1?.last_weight_kg ?? null} direction={s1?.last_weight_direction} formatter={formatKgNoDecimals} /></Table.Td>

                      <Table.Td><TrendValue value={s0?.sets_count ?? null} direction={s0?.sets_direction} formatter={formatSetsCount} /></Table.Td>
                      <Table.Td><TrendValue value={s0?.last_weight_kg ?? null} direction={s0?.last_weight_direction} formatter={formatKgNoDecimals} /></Table.Td>
                      <Table.Td><Sparkline values={sparklineSeries(row.byPeriod, (p) => p.volume_kg)} color="#8B93A6" /></Table.Td>
                    </Table.Tr>
                  );
                })}
              {equipmentComparativeRows.length === 0 && !recentVolumeQuery.isLoading && (
                <Table.Tr>
                  <Table.Td colSpan={11}>
                    <Text c={BRAND.textMuted} ta="center">Sem dados suficientes para o comparativo por equipamento.</Text>
                  </Table.Td>
                </Table.Tr>
              )}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
        )}
      </Paper>
      </Stack>
        </Tabs.Panel>

        <Tabs.Panel value="tendencia" pt="lg">
          <Stack gap="xl">
            <Group justify="flex-end">
              <Select
                label="Granularidade"
                value={granularity}
                onChange={(value) => setGranularity((value as TrainingVolumeGranularity) || 'd7')}
                data={granularityOptions}
                w={220}
              />
            </Group>

            {trainingVolumeQuery.isError && (
              <Alert color="red" icon={<IconAlertTriangle />} title="Erro ao carregar tendência de volume">
                {errorMessage(trainingVolumeQuery.error, 'Não foi possível consultar a Fitness API.')}
              </Alert>
            )}
            {trainingVolumeQuery.isLoading && (
              <Group>
                <Loader size="sm" color="brand" />
                <Text c={BRAND.textMuted}>Carregando tendência...</Text>
              </Group>
            )}

            {!trainingVolumeQuery.isLoading && !trainingVolumeQuery.isError && (
              <>
                <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
                  <Group justify="space-between" mb="xs">
                    <Title order={3} c={BRAND.textPrimary}>Repetições por {groupingLabelLower}</Title>
                    <Badge color="brand" variant="light">{windowLabels.title}: {windowLabels.currentLabel} x {windowLabels.previousLabel}</Badge>
                  </Group>
                  {repsChartData.length > 0 ? (
                    <RepsChart repsChartData={repsChartData} currentLabel={windowLabels.currentLabel} previousLabel={windowLabels.previousLabel} />
                  ) : (
                    <Text c={BRAND.textMuted}>Sem dados suficientes para o período.</Text>
                  )}
                </Paper>

                <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
                  <Group justify="space-between" mb="xs">
                    <Title order={3} c={BRAND.textPrimary}>Variação por grupamento</Title>
                    <Badge color="brand" variant="light">Aumento ou queda no período</Badge>
                  </Group>
                  <Text c={BRAND.textMuted} size="sm" mb="sm">
                    Janelas de {windowLabels.title.toLowerCase()} definidas pelo backend
                    {periodComparison
                      ? `: ${windowLabels.currentLabel.toLowerCase()} de ${formatSessionDate(periodComparison.current.from)} a ${formatSessionDate(periodComparison.current.to)} vs ${windowLabels.previousLabel.toLowerCase()} de ${formatSessionDate(periodComparison.previous.from)} a ${formatSessionDate(periodComparison.previous.to)}.`
                      : '.'}
                  </Text>
                  {RecordList ? (
                    <RecordList
                      label="Variação por grupamento"
                      emptyText="Sem dados para o período selecionado."
                      rows={repsComparison.map((item) => {
                        const trend = trendBadge(item.volume_direction);
                        return {
                          key: item.group.key,
                          title: item.group.label,
                          subtitle: trend.label,
                          value: `${item.volume_delta_kg >= 0 ? '+' : ''}${formatKgFixed(item.volume_delta_kg, 0)}`,
                          details: [
                            ['Volume atual', formatKgFixed(item.current_volume_kg, 0)],
                            ['Volume anterior', formatKgFixed(item.previous_volume_kg, 0)],
                            ['Variação de volume', `${item.volume_delta_kg >= 0 ? '+' : ''}${formatKgFixed(item.volume_delta_kg, 0)}`],
                            ['Leitura', trend.label],
                            ['Repetições atuais', formatFixed(item.current_reps, 0)],
                            ['Repetições anteriores', formatFixed(item.previous_reps, 0)],
                            ['Variação de repetições', `${item.reps_delta >= 0 ? '+' : ''}${formatFixed(item.reps_delta, 0)}`],
                          ],
                        };
                      })}
                    />
                  ) : (
                  <Table.ScrollContainer minWidth={800}>
                    <Table striped highlightOnHover withTableBorder verticalSpacing="sm">
                      <Table.Thead>
                        <Table.Tr>
                          <Table.Th>{groupingLabel}</Table.Th>
                          <Table.Th>Volume atual</Table.Th>
                          <Table.Th>Volume anterior</Table.Th>
                          <Table.Th>Variação de volume</Table.Th>
                          <Table.Th>Leitura</Table.Th>
                          <Table.Th>Repetições atuais</Table.Th>
                          <Table.Th>Repetições anteriores</Table.Th>
                          <Table.Th>Variação de repetições</Table.Th>
                        </Table.Tr>
                      </Table.Thead>
                      <Table.Tbody>
                        {repsComparison.map((item) => {
                          const trend = trendBadge(item.volume_direction);
                          return (
                            <Table.Tr key={item.group.key}>
                              <Table.Td><Text c={BRAND.textPrimary}>{item.group.label}</Text></Table.Td>
                              <Table.Td>{formatKgFixed(item.current_volume_kg, 0)}</Table.Td>
                              <Table.Td>{formatKgFixed(item.previous_volume_kg, 0)}</Table.Td>
                              <Table.Td>{item.volume_delta_kg >= 0 ? '+' : ''}{formatKgFixed(item.volume_delta_kg, 0)}</Table.Td>
                              <Table.Td><Badge color={trend.color} variant="light">{trend.label}</Badge></Table.Td>
                              <Table.Td>{formatFixed(item.current_reps, 0)}</Table.Td>
                              <Table.Td>{formatFixed(item.previous_reps, 0)}</Table.Td>
                              <Table.Td>{item.reps_delta >= 0 ? '+' : ''}{formatFixed(item.reps_delta, 0)}</Table.Td>
                            </Table.Tr>
                          );
                        })}
                        {repsComparison.length === 0 && (
                          <Table.Tr>
                            <Table.Td colSpan={8}>
                              <Text c={BRAND.textMuted} ta="center">Sem dados para o período selecionado.</Text>
                            </Table.Td>
                          </Table.Tr>
                        )}
                      </Table.Tbody>
                    </Table>
                  </Table.ScrollContainer>
                  )}
                </Paper>
              </>
            )}
          </Stack>
        </Tabs.Panel>

        <Tabs.Panel value="mapa-calor" pt="lg">
          <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
            <Group justify="space-between" mb="xs">
              <Title order={3} c={BRAND.textPrimary}>Mapa de calor por {groupingLabelLower}</Title>
              <Badge color="brand" variant="light">Tamanho: séries · Cor: carga média</Badge>
            </Group>
            <Group align="flex-end" mb="md">
              <Select
                label="Visualizar por"
                value={heatmapViewMode}
                onChange={(value) => setHeatmapViewMode((value as HeatmapViewMode) || 'session')}
                data={heatmapViewModeOptions}
                w={220}
              />
              {heatmapViewMode === 'session' && (
                <Select
                  label="Sessão de treino"
                  placeholder="Selecione um dia de treino"
                  value={selectedHeatmapSessionKey}
                  onChange={(value) => setHeatmapSessionKey(value)}
                  data={heatmapSessionOptions.map((session) => ({
                    value: session.key,
                    label: `${formatSessionDate(session.local_date)} · atividade #${session.activity_id}`,
                  }))}
                  w={320}
                />
              )}
              {heatmapViewMode === 'week' && (
                <TextInput
                  type="date"
                  label="Data de referência (últimos 7 dias)"
                  value={effectiveHeatmapRefDate}
                  onChange={(event) => setHeatmapRefDate(event.currentTarget.value || null)}
                  w={220}
                />
              )}
              {heatmapViewMode === 'month' && (
                <TextInput
                  type="month"
                  label="Mês"
                  value={effectiveHeatmapMonth}
                  onChange={(event) => setHeatmapMonth(event.currentTarget.value || null)}
                  w={220}
                />
              )}
            </Group>
            {heatmapIsLoading && <Loader size="sm" color="brand" />}
            {heatmapIsError && (
              <Alert color="red" icon={<IconAlertTriangle />}>Não foi possível carregar os dados do mapa de calor para o período selecionado.</Alert>
            )}
            {!heatmapIsLoading && !heatmapIsError && (
              heatmapCells.length === 0 ? (
                <Text c={BRAND.textMuted}>Sem dados para o período selecionado.</Text>
              ) : (
                <HeatmapTreemap heatmapTreemapData={heatmapTreemapData} />
              )
            )}
          </Paper>
        </Tabs.Panel>
      </Tabs>
    </Stack>
  );
}
