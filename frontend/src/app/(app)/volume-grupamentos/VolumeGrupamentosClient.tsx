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
  compareVolumeGroups,
  groupingKeyOf,
  isVolumeGroupingLevel,
  readVolumeGroupingLevel,
  writeVolumeGroupingLevel,
  type VolumeGroup,
  type VolumeGroupingLevel,
} from '@/lib/volume-grouping';
import type {
  ComparativeSetsRow,
  EquipmentComparativeRow,
  HeatmapViewMode,
  MuscleGroupComparativeRow,
  MuscleGroupWeeklyComparativeRow,
  MuscleHeatmapCell,
  RecentExerciseVolumeItem,
  RecentExerciseVolumeResponse,
  SlotComparativeSetsRow,
  SlotComparativeVolumeRow,
  SlotComparisonResponse,
  TrainingVolumeGranularity,
  TrainingVolumeResponse,
  TrendDirection,
  WorkoutPeriodKey,
} from '@/lib/training-volume-types';
import { slotComparisonCaption, slotComparisonPath, slotSetsRows, slotVolumeRows } from '@/lib/slot-comparison';
import { TrainingGoalBadge } from '@/components/TrainingGoalBadge';
import { ALL_GOALS, goalQueryParam, sessionGoalFilterOptions } from '@/lib/training-goal-filter';
import { useTrainingGoals } from '@/lib/training-goals';
import { recentExerciseVolumePath, volumeGrupamentosKeys, type RecentVolumeRange } from '@/lib/volume-grupamentos-queries';
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

// Utilitários de data — porte fiel de fitness-frontend/.../TrainingVolumeClient.tsx.
function parseDateKey(value: string): Date {
  return new Date(`${value}T12:00:00`);
}

function startOfDay(value: Date): Date {
  return new Date(value.getFullYear(), value.getMonth(), value.getDate());
}

function addDays(value: Date, days: number): Date {
  const next = new Date(value);
  next.setDate(next.getDate() + days);
  return startOfDay(next);
}

function startOfWeek(value: Date): Date {
  const day = value.getDay();
  const diff = day === 0 ? -6 : 1 - day;
  return addDays(startOfDay(value), diff);
}

function startOfMonth(value: Date): Date {
  return new Date(value.getFullYear(), value.getMonth(), 1);
}

function endOfDay(value: Date): Date {
  return new Date(value.getFullYear(), value.getMonth(), value.getDate(), 23, 59, 59, 999);
}

function daysBetweenInclusive(start: Date, end: Date): number {
  const msPerDay = 24 * 60 * 60 * 1000;
  return Math.floor((startOfDay(end).getTime() - startOfDay(start).getTime()) / msPerDay) + 1;
}

function inRange(date: Date, start: Date, end: Date): boolean {
  return date >= startOfDay(start) && date <= endOfDay(end);
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

// Segunda-feira da semana do treino, em formato YYYY-MM-DD — mesmo bucket
// usado por `training-volume-by-split-day` no backend (ver CLAUDE.md).
function weekKeyOf(dateStr: string): string {
  return startOfWeek(parseDateKey(dateStr)).toISOString().slice(0, 10);
}

// Janela current/previous por granularidade — porte fiel de `buildWindow`
// em fitness-frontend/.../TrainingVolumeClient.tsx (D-7 = últimos 7 dias vs
// 7 dias anteriores; semana = início da semana corrente até hoje vs mesmo
// número de dias da semana anterior; mês = início do mês corrente até hoje
// vs mesmo número de dias no mês anterior, truncado ao tamanho do mês
// anterior quando necessário).
function buildWindow(granularity: TrainingVolumeGranularity) {
  const today = startOfDay(new Date());

  if (granularity === 'd7') {
    const currentEnd = today;
    const currentStart = addDays(today, -6);
    const previousEnd = addDays(currentStart, -1);
    const previousStart = addDays(previousEnd, -6);

    return {
      currentStart,
      currentEnd,
      previousStart,
      previousEnd,
      currentLabel: 'D-7 atual',
      previousLabel: 'D-7 anterior',
      title: 'D-7',
    };
  }

  if (granularity === 'week') {
    const currentStart = startOfWeek(today);
    const currentEnd = today;
    const spanDays = daysBetweenInclusive(currentStart, currentEnd);
    const previousEnd = addDays(currentStart, -1);
    const previousStart = addDays(previousEnd, -(spanDays - 1));

    return {
      currentStart,
      currentEnd,
      previousStart,
      previousEnd,
      currentLabel: 'Semana atual',
      previousLabel: 'Semana anterior',
      title: 'Semana x Semana',
    };
  }

  const currentStart = startOfMonth(today);
  const currentEnd = today;
  const spanDays = daysBetweenInclusive(currentStart, currentEnd);
  const previousMonthEnd = addDays(currentStart, -1);
  const previousMonthStart = startOfMonth(previousMonthEnd);
  const previousMonthSpan = daysBetweenInclusive(previousMonthStart, previousMonthEnd);
  const previousEnd = addDays(previousMonthStart, Math.min(spanDays, previousMonthSpan) - 1);

  return {
    currentStart,
    currentEnd,
    previousStart: previousMonthStart,
    previousEnd,
    currentLabel: 'Mês atual',
    previousLabel: 'Mês anterior',
    title: 'Mês x Mês',
  };
}

function getTrendBadge(delta: number) {
  if (delta > 0) return { color: 'green', label: 'Aumentou' };
  if (delta < 0) return { color: 'red', label: 'Diminuiu' };
  return { color: 'gray', label: 'Estável' };
}

function getTrendDirection(current: number | null, previous: number | null, epsilon = 0.0001): TrendDirection {
  if (current == null || previous == null) return 'none';
  const delta = current - previous;
  if (Math.abs(delta) <= epsilon) return 'stable';
  return delta > 0 ? 'up' : 'down';
}

// Setas + cores de tendência (verde/vermelho semânticos padrão do Mantine —
// BRAND não define tons de verde, e a paleta padrão contrasta bem sobre o
// fundo escuro do tema). Estável usa a cor de acento da marca (laranja).
function TrendValue({
  current,
  previous,
  formatter,
  epsilon = 0.0001,
}: {
  current: number | null;
  previous: number | null;
  formatter: (value: number | null) => string;
  epsilon?: number;
}) {
  const direction = getTrendDirection(current, previous, epsilon);
  const label = formatter(current);

  if (direction === 'none') {
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

// Bloco 6: sparkline por linha nos comparativos — mini-LineChart do
// recharts, sem eixo/grid.
function sparklineSeriesFromVolume(row: MuscleGroupComparativeRow): Array<number | null> {
  return [
    row.byPeriod.t3?.volume_kg ?? null,
    row.byPeriod.t2?.volume_kg ?? null,
    row.byPeriod.t1?.volume_kg ?? null,
    row.byPeriod.t0?.volume_kg ?? null,
  ];
}

function sparklineSeriesFromSets(row: ComparativeSetsRow): Array<number | null> {
  return [
    row.byPeriod.t3?.sets_count ?? null,
    row.byPeriod.t2?.sets_count ?? null,
    row.byPeriod.t1?.sets_count ?? null,
    row.byPeriod.t0?.sets_count ?? null,
  ];
}

function sparklineSeriesFromEquipment(row: EquipmentComparativeRow): Array<number | null> {
  return [
    row.byWeek.t3?.volume ?? null,
    row.byWeek.t2?.volume ?? null,
    row.byWeek.t1?.volume ?? null,
    row.byWeek.t0?.volume ?? null,
  ];
}

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

async function fetchRecentExerciseVolume(
  range: RecentVolumeRange | null,
  goal: string | null,
): Promise<RecentExerciseVolumeItem[]> {
  const response = await apiFetch<RecentExerciseVolumeResponse>(
    recentExerciseVolumePath(RECENT_VOLUME_LIMIT, range, goal),
  );
  return response.items;
}

export function VolumeGrupamentosClient() {
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
    queryKey: volumeGrupamentosKeys.recentVolume(RECENT_VOLUME_LIMIT, goal),
    queryFn: () => fetchRecentExerciseVolume(null, goal),
  });

  // Referência estável: os memos abaixo (agora também por nível) dependem dela.
  const rows = useMemo(() => recentVolumeQuery.data || [], [recentVolumeQuery.data]);

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

  // Bloco 2: comparativo semanal S-3..S-0 — mesma lógica do comparativo por
  // sessão abaixo, mas com bucket semanal (weekKeyOf) em vez de por
  // activity_id; sessões da mesma semana somam no mesmo ponto.
  const comparativeWeekRows = useMemo<MuscleGroupWeeklyComparativeRow[]>(() => {
    if (!rows.length) return [];

    const byGroup = new Map<
      string,
      { group: VolumeGroup; weeks: Map<string, { week_key: string; volume: number; rpe_sum: number; rpe_count: number }> }
    >();

    for (const item of rows) {
      const group = groupingKeyOf(item, groupingLevel);
      const weekKey = weekKeyOf(item.local_date);
      const entry = byGroup.get(group.key) ?? { group, weeks: new Map() };

      const current = entry.weeks.get(weekKey) ?? { week_key: weekKey, volume: 0, rpe_sum: 0, rpe_count: 0 };
      current.volume += Number(item.volume_kg || 0);
      if (item.avg_rpe != null) {
        current.rpe_sum += Number(item.avg_rpe) * item.sets_count;
        current.rpe_count += item.sets_count;
      }

      entry.weeks.set(weekKey, current);
      byGroup.set(group.key, entry);
    }

    const periodKeys: WorkoutPeriodKey[] = ['t0', 't1', 't2', 't3'];
    const result: MuscleGroupWeeklyComparativeRow[] = [];

    for (const { group, weeks } of byGroup.values()) {
      const sorted = Array.from(weeks.values()).sort((left, right) => right.week_key.localeCompare(left.week_key));
      const lastFourWeeks = sorted.slice(0, 4);
      const row: MuscleGroupWeeklyComparativeRow = { group, byPeriod: { t3: null, t2: null, t1: null, t0: null } };

      lastFourWeeks.forEach((week, index) => {
        row.byPeriod[periodKeys[index]] = {
          activity_id: 0,
          local_date: week.week_key,
          volume_kg: week.volume,
          avg_rpe: week.rpe_count > 0 ? week.rpe_sum / week.rpe_count : null,
        };
      });

      result.push(row);
    }

    return result.sort((left, right) => compareVolumeGroups(left.group, right.group));
  }, [rows, groupingLevel]);

  // Bloco 3: comparativo por equipamento (últimas 4 semanas), expansível
  // pela chave do nível — uma linha "Total" seguida das linhas de cada
  // equipamento (últimos 4 buckets semanais em que a chave apareceu).
  const equipmentComparativeRows = useMemo<EquipmentComparativeRow[]>(() => {
    if (!rows.length) return [];

    const groupsByKey = new Map<string, VolumeGroup>();
    const weeksByGroup = new Map<string, Set<string>>();
    for (const item of rows) {
      const group = groupingKeyOf(item, groupingLevel);
      groupsByKey.set(group.key, group);
      const weekKey = weekKeyOf(item.local_date);
      const weekSet = weeksByGroup.get(group.key) || new Set<string>();
      weekSet.add(weekKey);
      weeksByGroup.set(group.key, weekSet);
    }

    const topWeeksByGroup = new Map<string, string[]>();
    for (const [groupKey, weekSet] of weeksByGroup.entries()) {
      const sorted = Array.from(weekSet).sort((left, right) => right.localeCompare(left));
      topWeeksByGroup.set(groupKey, sorted.slice(0, 4));
    }

    type Agg = { volume: number; sets_count: number; last_weight: number | null; last_order: string };
    const byGroupEquipmentWeek = new Map<string, Map<string, Map<string, Agg>>>();

    for (const item of rows) {
      const groupKey = groupingKeyOf(item, groupingLevel).key;
      const equipmentLabel = item.equipment_name || 'Sem equipamento';
      const weekKey = weekKeyOf(item.local_date);
      const topWeeks = topWeeksByGroup.get(groupKey) || [];
      if (!topWeeks.includes(weekKey)) continue;

      // orderKey por (local_date, activity_id) — cada item já é por
      // atividade inteira (não por série), então last_weight vem do
      // last_weight_kg da atividade mais recente dentro da semana.
      const orderKey = `${item.local_date}_${String(item.activity_id).padStart(10, '0')}`;

      const equipmentMap = byGroupEquipmentWeek.get(groupKey) || new Map<string, Map<string, Agg>>();
      const weekMap = equipmentMap.get(equipmentLabel) || new Map<string, Agg>();
      const agg = weekMap.get(weekKey) || { volume: 0, sets_count: 0, last_weight: null, last_order: '' };

      agg.volume += item.volume_kg;
      agg.sets_count += item.sets_count;
      if (orderKey >= agg.last_order) {
        agg.last_order = orderKey;
        agg.last_weight = item.last_weight_kg;
      }

      weekMap.set(weekKey, agg);
      equipmentMap.set(equipmentLabel, weekMap);
      byGroupEquipmentWeek.set(groupKey, equipmentMap);
    }

    const periodKeys: WorkoutPeriodKey[] = ['t0', 't1', 't2', 't3'];
    const groupBlocks: Array<{ group: VolumeGroup; rows: EquipmentComparativeRow[] }> = [];

    for (const [groupKey, equipmentMap] of byGroupEquipmentWeek.entries()) {
      const group = groupsByKey.get(groupKey) as VolumeGroup;
      const topWeeks = topWeeksByGroup.get(groupKey) || [];

      const equipmentRows: EquipmentComparativeRow[] = [];
      for (const [equipmentLabel, weekMap] of equipmentMap.entries()) {
        const byWeek: Record<WorkoutPeriodKey, { sets_count: number; last_weight: number | null; volume: number } | null> = {
          t3: null,
          t2: null,
          t1: null,
          t0: null,
        };
        topWeeks.forEach((weekKey, index) => {
          const periodKey = periodKeys[index];
          const agg = weekMap.get(weekKey);
          if (agg) byWeek[periodKey] = { sets_count: agg.sets_count, last_weight: agg.last_weight, volume: agg.volume };
        });
        equipmentRows.push({ group, equipment_label: equipmentLabel, isTotal: false, isFirstOfGroup: false, groupSize: 0, byWeek });
      }

      equipmentRows.sort((left, right) => {
        const totalLeft = periodKeys.reduce((acc, key) => acc + (left.byWeek[key]?.sets_count ?? 0), 0);
        const totalRight = periodKeys.reduce((acc, key) => acc + (right.byWeek[key]?.sets_count ?? 0), 0);
        if (totalRight !== totalLeft) return totalRight - totalLeft;
        return left.equipment_label.localeCompare(right.equipment_label);
      });

      const totalByWeek: Record<WorkoutPeriodKey, { sets_count: number; last_weight: number | null; volume: number } | null> = {
        t3: null,
        t2: null,
        t1: null,
        t0: null,
      };
      for (const periodKey of periodKeys) {
        let setsSum = 0;
        let weightSum = 0;
        let volumeSum = 0;
        let any = false;
        for (const equipmentRow of equipmentRows) {
          const metrics = equipmentRow.byWeek[periodKey];
          if (metrics) {
            any = true;
            setsSum += metrics.sets_count;
            weightSum += metrics.last_weight ?? 0;
            volumeSum += metrics.volume;
          }
        }
        totalByWeek[periodKey] = any ? { sets_count: setsSum, last_weight: weightSum, volume: volumeSum } : null;
      }

      const groupRows: EquipmentComparativeRow[] = [
        { group, equipment_label: 'Total', isTotal: true, isFirstOfGroup: true, groupSize: equipmentRows.length + 1, byWeek: totalByWeek },
        ...equipmentRows,
      ];

      groupBlocks.push({ group, rows: groupRows });
    }

    groupBlocks.sort((left, right) => compareVolumeGroups(left.group, right.group));
    return groupBlocks.flatMap((block) => block.rows);
  }, [rows, groupingLevel]);

  // Bloco 4: gráfico de repetições + tabela "Variação por grupamento" +
  // granularidade — GET /analytics/training-volume?days=... (mesma regra de
  // `queryDaysByGranularity`/`buildWindow` da referência on-premise).
  const queryDays = queryDaysByGranularity[granularity];
  const trainingVolumeQuery = useQuery({
    queryKey: ['training-volume', queryDays],
    queryFn: () => apiFetch<TrainingVolumeResponse>(`/analytics/training-volume?days=${queryDays}`),
  });

  const window = useMemo(() => buildWindow(granularity), [granularity]);

  const repsComparison = useMemo(() => {
    type RepsRow = {
      group: VolumeGroup;
      current_reps: number;
      previous_reps: number;
      reps_delta: number;
      current_volume: number;
      previous_volume: number;
      volume_delta: number;
    };
    const grouped = new Map<string, RepsRow>();

    for (const item of trainingVolumeQuery.data?.trend.items || []) {
      const group = groupingKeyOf(item, groupingLevel);
      const row = grouped.get(group.key) || {
        group,
        current_reps: 0,
        previous_reps: 0,
        reps_delta: 0,
        current_volume: 0,
        previous_volume: 0,
        volume_delta: 0,
      };

      const date = parseDateKey(item.period);
      if (inRange(date, window.currentStart, window.currentEnd)) {
        row.current_reps += Number(item.reps_total || 0);
        row.current_volume += Number(item.volume_kg || 0);
      } else if (inRange(date, window.previousStart, window.previousEnd)) {
        row.previous_reps += Number(item.reps_total || 0);
        row.previous_volume += Number(item.volume_kg || 0);
      }

      grouped.set(group.key, row);
    }

    return Array.from(grouped.values())
      .map((item) => ({
        ...item,
        reps_delta: item.current_reps - item.previous_reps,
        volume_delta: item.current_volume - item.previous_volume,
      }))
      // Macro-grupos na ordem do cadastro; grupo e grupo pai seguem o ranking
      // por repetições de sempre.
      .sort((left, right) =>
        groupingLevel === 'volume_class'
          ? compareVolumeGroups(left.group, right.group)
          : right.current_reps - left.current_reps || right.current_volume - left.current_volume,
      );
  }, [trainingVolumeQuery.data, window, groupingLevel]);

  const repsChartData = useMemo(
    () => repsComparison.map((item) => ({ grupo: item.group.label, atual: item.current_reps, anterior: item.previous_reps })),
    [repsComparison],
  );

  // Bloco 5: mapa de calor — sessão usa `rows` (já carregado por
  // recentVolumeQuery); semana/mês buscam um conjunto dedicado filtrado por
  // date_from/date_to no backend (mesmo endpoint), pois o range pode
  // extrapolar os ~400 exercícios já buscados para as demais tabelas.
  const recentVolumeRowsSorted = useMemo(() => {
    if (!rows.length) return [];
    return [...rows].sort((left, right) => {
      if (left.local_date !== right.local_date) return right.local_date.localeCompare(left.local_date);
      return right.activity_id - left.activity_id;
    });
  }, [rows]);

  const heatmapSessionOptions = useMemo(() => {
    if (!recentVolumeRowsSorted.length) return [];

    const byKey = new Map<string, { activity_id: number; local_date: string }>();
    for (const item of recentVolumeRowsSorted) {
      const key = `${item.local_date}|${item.activity_id}`;
      if (!byKey.has(key)) byKey.set(key, { activity_id: item.activity_id, local_date: item.local_date });
    }

    return Array.from(byKey.entries())
      .map(([key, session]) => ({ key, ...session }))
      .sort((left, right) => {
        if (left.local_date !== right.local_date) return right.local_date.localeCompare(left.local_date);
        return right.activity_id - left.activity_id;
      });
  }, [recentVolumeRowsSorted]);

  const selectedHeatmapSessionKey = heatmapSessionKey ?? heatmapSessionOptions[0]?.key ?? null;
  const latestAvailableDate = recentVolumeRowsSorted[0]?.local_date || null;

  const effectiveHeatmapRefDate = heatmapRefDate ?? latestAvailableDate ?? todayDateKey();
  const effectiveHeatmapMonth = heatmapMonth ?? (latestAvailableDate ? monthKeyOf(latestAvailableDate) : monthKeyOf(todayDateKey()));

  const heatmapWeekDateFrom = useMemo(() => toDateKey(addDays(parseDateKey(effectiveHeatmapRefDate), -6)), [effectiveHeatmapRefDate]);
  const heatmapWeekDateTo = effectiveHeatmapRefDate;

  const heatmapMonthDateFrom = `${effectiveHeatmapMonth}-01`;
  const heatmapMonthDateTo = lastDayOfMonthKey(effectiveHeatmapMonth);

  const heatmapRangeDateFrom = heatmapViewMode === 'week' ? heatmapWeekDateFrom : heatmapViewMode === 'month' ? heatmapMonthDateFrom : null;
  const heatmapRangeDateTo = heatmapViewMode === 'week' ? heatmapWeekDateTo : heatmapViewMode === 'month' ? heatmapMonthDateTo : null;

  const heatmapRangeQuery = useQuery({
    queryKey: volumeGrupamentosKeys.recentVolumeRange(heatmapRangeDateFrom, heatmapRangeDateTo, goal),
    queryFn: () => fetchRecentExerciseVolume({ dateFrom: heatmapRangeDateFrom!, dateTo: heatmapRangeDateTo! }, goal),
    enabled: heatmapViewMode !== 'session' && !!heatmapRangeDateFrom && !!heatmapRangeDateTo,
  });

  const heatmapSourceRows = heatmapViewMode === 'session' ? recentVolumeRowsSorted : (heatmapRangeQuery.data || []);
  const heatmapIsLoading = heatmapViewMode === 'session' ? recentVolumeQuery.isLoading : heatmapRangeQuery.isLoading;
  const heatmapIsError = heatmapViewMode === 'session' ? recentVolumeQuery.isError : heatmapRangeQuery.isError;

  const heatmapCells = useMemo(() => {
    if (heatmapViewMode === 'session' && !selectedHeatmapSessionKey) return [];

    const grouped = new Map<string, { group: VolumeGroup; sets_count: number; volume_kg: number }>();
    for (const item of heatmapSourceRows) {
      if (heatmapViewMode === 'session') {
        const [sessionDate, sessionActivityIdStr] = (selectedHeatmapSessionKey || '').split('|');
        const sessionActivityId = Number(sessionActivityIdStr);
        if (item.local_date !== sessionDate || item.activity_id !== sessionActivityId) continue;
      } else if (heatmapRangeDateFrom && heatmapRangeDateTo) {
        if (item.local_date < heatmapRangeDateFrom || item.local_date > heatmapRangeDateTo) continue;
      }

      const group = groupingKeyOf(item, groupingLevel);
      const current = grouped.get(group.key) || { group, sets_count: 0, volume_kg: 0 };
      current.sets_count += item.sets_count;
      current.volume_kg += item.volume_kg;
      grouped.set(group.key, current);
    }

    // avg_weight_kg aqui é uma aproximação (volume_kg / sets_count) — o
    // endpoint agregado não expõe mais o peso bruto série a série; usado só
    // como sinal relativo para o gradiente de cor do treemap.
    const cells: MuscleHeatmapCell[] = Array.from(grouped.values()).map((agg) => ({
      group: agg.group,
      sets_count: agg.sets_count,
      avg_weight_kg: agg.sets_count > 0 ? agg.volume_kg / agg.sets_count : null,
      volume_kg: agg.volume_kg,
    }));

    return cells.sort((left, right) => right.sets_count - left.sets_count);
  }, [heatmapSourceRows, heatmapViewMode, selectedHeatmapSessionKey, heatmapRangeDateFrom, heatmapRangeDateTo, groupingLevel]);

  const heatmapVolumeRange = useMemo(() => {
    const volumes = heatmapCells.map((cell) => cell.volume_kg).filter((value): value is number => value != null);
    if (!volumes.length) return { minVolume: 0, maxVolume: 0 };
    return { minVolume: Math.min(...volumes), maxVolume: Math.max(...volumes) };
  }, [heatmapCells]);

  const heatmapTreemapData = useMemo(
    () => heatmapCells.map((cell) => ({
      name: cell.group.label,
      sets_count: cell.sets_count,
      avg_weight_kg: cell.avg_weight_kg,
      volume_kg: cell.volume_kg,
      minVolume: heatmapVolumeRange.minVolume,
      maxVolume: heatmapVolumeRange.maxVolume,
    })),
    [heatmapCells, heatmapVolumeRange],
  );

  // Comparativo T-0..T-3 no nível escolhido (tabela e gráfico "T-0 vs T-1").
  const comparativeRows = useMemo<SlotComparativeVolumeRow[]>(
    () => slotVolumeRows(slotComparisonQuery.data, groupingLevel),
    [slotComparisonQuery.data, groupingLevel],
  );
  // Cards de resumo e "Volume total" seguem por GRUPO em qualquer nível: são
  // totais da página, e com o T-k contado por linha a soma mudaria com o seletor.
  const comparativeRowsByGroup = useMemo(
    () => slotVolumeRows(slotComparisonQuery.data, 'muscle_group'),
    [slotComparisonQuery.data],
  );

  const kpis = useMemo(() => {
    let groupsWithT0 = 0;
    let totalVolumeT0 = 0;
    let rpeWeightedSum = 0;
    let rpeWeightedCount = 0;

    for (const row of comparativeRowsByGroup) {
      const t0 = row.byPeriod.t0;
      if (!t0) continue;
      groupsWithT0 += 1;
      totalVolumeT0 += t0.volume_kg;
      if (t0.avg_rpe != null) {
        rpeWeightedSum += t0.avg_rpe;
        rpeWeightedCount += 1;
      }
    }

    return {
      groupsWithT0,
      totalVolumeT0,
      avgRpeT0: rpeWeightedCount > 0 ? rpeWeightedSum / rpeWeightedCount : null,
      groupsTracked: comparativeRowsByGroup.length,
    };
  }, [comparativeRowsByGroup]);

  // Volume T-0 (última sessão) vs T-1 (sessão anterior) pela chave do nível —
  // mesmo padrão do gráfico "atual x anterior" da versão de referência
  // (fitness-frontend/.../TrainingVolumeClient.tsx), adaptado ao dado já
  // comparativo desta tela (byPeriod T-3..T-0). Ordenado por volume T-0
  // desc e limitado aos 10 grupos mais treinados, para caber no mobile.
  const volumeByGroupChartData = useMemo(
    () =>
      comparativeRows
        .map((row) => ({
          grupo: row.group.label,
          t0: row.byPeriod.t0?.volume_kg ?? 0,
          t1: row.byPeriod.t1?.volume_kg ?? 0,
        }))
        .sort((left, right) => right.t0 - left.t0)
        .slice(0, 10),
    [comparativeRows],
  );

  // Volume total agregado (soma de todos os grupos) por período T-3..T-0 —
  // leitura geral de tendência de carga total nas últimas 4 sessões.
  const totalVolumeTrendChartData = useMemo(() => {
    const periods: WorkoutPeriodKey[] = ['t3', 't2', 't1', 't0'];
    const labels: Record<WorkoutPeriodKey, string> = { t3: 'T-3', t2: 'T-2', t1: 'T-1', t0: 'T-0' };
    return periods.map((period) => ({
      periodo: labels[period],
      volume: comparativeRowsByGroup.reduce((acc, row) => acc + (row.byPeriod[period]?.volume_kg ?? 0), 0),
    }));
  }, [comparativeRowsByGroup]);

  return (
    <Stack gap="xl">
      <Group justify="space-between" align="flex-end">
        <div>
          <Title order={1} c={BRAND.textPrimary}>
            Volume por grupamento
          </Title>
          <Text c={BRAND.textMuted} size="md">
            Comparativo de volume e RPE médio nas últimas 4 sessões (T-3 a T-0), com indicação de tendência.
          </Text>
        </div>
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
            {kpis.groupsWithT0} / {kpis.groupsTracked}
          </Title>
        </Paper>
        <Paper withBorder radius="lg" p="md" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
          <Text size="xs" c={BRAND.textMuted} tt="uppercase" fw={600}>
            Volume na última sessão (T-0)
          </Text>
          <Title order={2} c={BRAND.textPrimary}>
            {formatKgFixed(kpis.totalVolumeT0, 0)}
          </Title>
        </Paper>
        <Paper withBorder radius="lg" p="md" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
          <Text size="xs" c={BRAND.textMuted} tt="uppercase" fw={600}>
            RPE médio (T-0)
          </Text>
          <Title order={2} c={BRAND.textPrimary}>
            {kpis.avgRpeT0 != null ? formatFixed(kpis.avgRpeT0, 1) : '-'}
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
                      <TrendValue current={t2?.volume_kg ?? null} previous={t3?.volume_kg ?? null} formatter={formatKgNoDecimals} />
                    </Table.Td>
                    <Table.Td>
                      <TrendValue current={t2?.avg_rpe ?? null} previous={t3?.avg_rpe ?? null} formatter={formatRpeOneDecimal} epsilon={0.05} />
                    </Table.Td>

                    <Table.Td>
                      <TrendValue current={t1?.volume_kg ?? null} previous={t2?.volume_kg ?? null} formatter={formatKgNoDecimals} />
                    </Table.Td>
                    <Table.Td>
                      <TrendValue current={t1?.avg_rpe ?? null} previous={t2?.avg_rpe ?? null} formatter={formatRpeOneDecimal} epsilon={0.05} />
                    </Table.Td>

                    <Table.Td>
                      <TrendValue current={t0?.volume_kg ?? null} previous={t1?.volume_kg ?? null} formatter={formatKgNoDecimals} />
                    </Table.Td>
                    <Table.Td>
                      <TrendValue current={t0?.avg_rpe ?? null} previous={t1?.avg_rpe ?? null} formatter={formatRpeOneDecimal} epsilon={0.05} />
                    </Table.Td>
                    <Table.Td><Sparkline values={sparklineSeriesFromVolume(row)} /></Table.Td>
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
                    <Table.Td><TrendValue current={t2?.sets_count ?? null} previous={t3?.sets_count ?? null} formatter={formatSetsCount} /></Table.Td>
                    <Table.Td><TrendValue current={t1?.sets_count ?? null} previous={t2?.sets_count ?? null} formatter={formatSetsCount} /></Table.Td>
                    <Table.Td><TrendValue current={t0?.sets_count ?? null} previous={t1?.sets_count ?? null} formatter={formatSetsCount} /></Table.Td>
                    <Table.Td><Sparkline values={sparklineSeriesFromSets(row)} /></Table.Td>
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
      </Paper>

      <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
        <Group justify="space-between" mb="xs">
          <Title order={3} c={BRAND.textPrimary}>
            Comparativo das últimas 4 semanas por {groupingLabelLower}
          </Title>
        </Group>
        <Text c={BRAND.textMuted} size="sm" mb="sm">
          Mesma agregação do comparativo por sessão, com bucket semanal (segunda-feira da semana) em vez de por
          treino — sessões da mesma semana somam no mesmo ponto (S-3 mais antiga, S-0 atual).
        </Text>
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
                const s3 = row.byPeriod.t3;
                const s2 = row.byPeriod.t2;
                const s1 = row.byPeriod.t1;
                const s0 = row.byPeriod.t0;

                return (
                  <Table.Tr key={row.group.key}>
                    <Table.Td><Text fw={600} c={BRAND.textPrimary}>{row.group.label}</Text></Table.Td>

                    <Table.Td>{formatKgFixed(s3?.volume_kg ?? null, 0)}</Table.Td>
                    <Table.Td>{formatFixed(s3?.avg_rpe ?? null, 1)}</Table.Td>

                    <Table.Td><TrendValue current={s2?.volume_kg ?? null} previous={s3?.volume_kg ?? null} formatter={formatKgNoDecimals} /></Table.Td>
                    <Table.Td><TrendValue current={s2?.avg_rpe ?? null} previous={s3?.avg_rpe ?? null} formatter={formatRpeOneDecimal} epsilon={0.05} /></Table.Td>

                    <Table.Td><TrendValue current={s1?.volume_kg ?? null} previous={s2?.volume_kg ?? null} formatter={formatKgNoDecimals} /></Table.Td>
                    <Table.Td><TrendValue current={s1?.avg_rpe ?? null} previous={s2?.avg_rpe ?? null} formatter={formatRpeOneDecimal} epsilon={0.05} /></Table.Td>

                    <Table.Td><TrendValue current={s0?.volume_kg ?? null} previous={s1?.volume_kg ?? null} formatter={formatKgNoDecimals} /></Table.Td>
                    <Table.Td><TrendValue current={s0?.avg_rpe ?? null} previous={s1?.avg_rpe ?? null} formatter={formatRpeOneDecimal} epsilon={0.05} /></Table.Td>
                    <Table.Td><Sparkline values={sparklineSeriesFromVolume(row)} color={BRAND.red} /></Table.Td>
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
                  const s3 = row.byWeek.t3;
                  const s2 = row.byWeek.t2;
                  const s1 = row.byWeek.t1;
                  const s0 = row.byWeek.t0;
                  const isExpanded = expandedEquipmentGroups.has(row.group.key);
                  const rowSpan = isExpanded ? row.groupSize : 1;

                  return (
                    <Table.Tr key={`${row.group.key}-${row.equipment_label}-${index}`}>
                      {row.isFirstOfGroup && (
                        <Table.Td rowSpan={rowSpan} style={{ verticalAlign: 'top' }}>
                          <Text fw={600} c={BRAND.textPrimary}>{row.group.label}</Text>
                        </Table.Td>
                      )}
                      <Table.Td>
                        {row.isTotal ? (
                          <Group gap={4} wrap="nowrap">
                            <Text fw={700} size="sm" c={BRAND.textPrimary}>{row.equipment_label}</Text>
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
                          <Text c={BRAND.textMuted}>{row.equipment_label}</Text>
                        )}
                      </Table.Td>

                      <Table.Td>{formatSetsCount(s3?.sets_count ?? null)}</Table.Td>
                      <Table.Td>{formatKgFixed(s3?.last_weight ?? null, 0)}</Table.Td>

                      <Table.Td><TrendValue current={s2?.sets_count ?? null} previous={s3?.sets_count ?? null} formatter={formatSetsCount} /></Table.Td>
                      <Table.Td><TrendValue current={s2?.last_weight ?? null} previous={s3?.last_weight ?? null} formatter={formatKgNoDecimals} /></Table.Td>

                      <Table.Td><TrendValue current={s1?.sets_count ?? null} previous={s2?.sets_count ?? null} formatter={formatSetsCount} /></Table.Td>
                      <Table.Td><TrendValue current={s1?.last_weight ?? null} previous={s2?.last_weight ?? null} formatter={formatKgNoDecimals} /></Table.Td>

                      <Table.Td><TrendValue current={s0?.sets_count ?? null} previous={s1?.sets_count ?? null} formatter={formatSetsCount} /></Table.Td>
                      <Table.Td><TrendValue current={s0?.last_weight ?? null} previous={s1?.last_weight ?? null} formatter={formatKgNoDecimals} /></Table.Td>
                      <Table.Td><Sparkline values={sparklineSeriesFromEquipment(row)} color="#8B93A6" /></Table.Td>
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
                    <Badge color="brand" variant="light">{window.title}: {window.currentLabel} x {window.previousLabel}</Badge>
                  </Group>
                  {repsChartData.length > 0 ? (
                    <RepsChart repsChartData={repsChartData} currentLabel={window.currentLabel} previousLabel={window.previousLabel} />
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
                    GET /analytics/training-volume?days={queryDays} — janelas de {window.title.toLowerCase()}{' '}
                    calculadas no cliente ({window.currentLabel.toLowerCase()} vs {window.previousLabel.toLowerCase()}).
                  </Text>
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
                          const trend = getTrendBadge(item.volume_delta);
                          return (
                            <Table.Tr key={item.group.key}>
                              <Table.Td><Text c={BRAND.textPrimary}>{item.group.label}</Text></Table.Td>
                              <Table.Td>{formatKgFixed(item.current_volume, 0)}</Table.Td>
                              <Table.Td>{formatKgFixed(item.previous_volume, 0)}</Table.Td>
                              <Table.Td>{item.volume_delta >= 0 ? '+' : ''}{formatKgFixed(item.volume_delta, 0)}</Table.Td>
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
