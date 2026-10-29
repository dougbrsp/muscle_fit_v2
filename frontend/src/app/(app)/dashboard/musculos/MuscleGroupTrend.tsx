'use client';

import {
  Badge,
  Button,
  Checkbox,
  Group,
  MultiSelect,
  Paper,
  Select,
  Table,
  Text,
  Title,
} from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import dynamic from 'next/dynamic';
import { useMemo, useState } from 'react';
import { ChartPlaceholder } from '@/components/charts/ChartPlaceholder';
import { DashboardSkeleton, EmptyState, ErrorState } from '@/components/dashboard/states';
import { apiFetch } from '@/lib/api';
import {
  DEFAULT_MUSCLE_GROUP_TREND_FILTERS,
  TREND_DAY_OPTIONS,
  TREND_METRIC_OPTIONS,
  TREND_PERIOD_DESCRIPTION,
  TREND_PERIOD_OPTIONS,
  formatTrendMetric,
  isTrendMetric,
  isTrendPeriod,
  muscleGroupTrendPath,
  muscleGroupTrendQueryKey,
  pivotTrend,
  pressureRows as buildPressureRows,
  trendGroupOptions as buildTrendGroupOptions,
  trendGroupTotals as buildTrendGroupTotals,
  trendPeriods as buildTrendPeriods,
  trendValueIndex,
  type MuscleGroupTrendFilters,
} from '@/lib/muscle-group-trend';
import { DEFAULT_VISIBLE_GROUPS, keepAvailableGroups, resolveVisibleGroups } from '@/lib/muscle-groups-selection';
import type { MuscleGroupsMappedPayload } from '@/lib/muscle-groups-types';
import { BRAND } from '@/theme';

// COD-14: recharts sob demanda; o placeholder tem a altura do gráfico (300).
const MuscleGroupTrendChart = dynamic(() => import('./MuscleGroupTrendChart').then((m) => m.MuscleGroupTrendChart), {
  ssr: false,
  loading: () => <ChartPlaceholder height={300} />,
});

/**
 * "Tendência por grupo muscular" — gráfico de linhas por grupo ao longo do
 * tempo + ranking de pressão no período mais recente, sobre
 * `GET /analytics/muscle-groups-mapped-trend` (classificação canônica via
 * exercise_catalog). Veio de `/grupos-musculares`.
 *
 * Filtros PRÓPRIOS (granularidade, métrica, janela em dias, grupos
 * secundários e grupos exibidos), independentes do período global do hub:
 * o endpoint trabalha com janela em dias e granularidade, não com o
 * intervalo de datas de `useDashboardFilters`.
 *
 * Seleção de grupos: a seleção exibida é DERIVADA (seleção ∩ opções) com
 * useMemo — nunca podada por efeito + setState, que entrava em loop de
 * render (React #185, corrigido no PR #290).
 */
export function MuscleGroupTrend({
  initialFilters = DEFAULT_MUSCLE_GROUP_TREND_FILTERS,
}: {
  initialFilters?: MuscleGroupTrendFilters;
}) {
  const [filters, setFilters] = useState<MuscleGroupTrendFilters>(initialFilters);
  const { period, metric, days, includeSecondary } = filters;
  const updateFilters = (patch: Partial<MuscleGroupTrendFilters>) =>
    setFilters((current) => ({ ...current, ...patch }));

  const trendQuery = useQuery({
    queryKey: muscleGroupTrendQueryKey(filters),
    queryFn: () => apiFetch<MuscleGroupsMappedPayload>(muscleGroupTrendPath(filters)),
  });

  // Memoizado sobre `data`: um `?? []` solto criaria um array novo a cada
  // render enquanto a query carrega, e todos os memos abaixo (inclusive as
  // opções do MultiSelect) mudariam de identidade sempre.
  const trendRows = useMemo(() => trendQuery.data?.trend_rows ?? [], [trendQuery.data]);
  const totals = useMemo(() => buildTrendGroupTotals(trendRows), [trendRows]);
  const groupOptions = useMemo(() => buildTrendGroupOptions(totals), [totals]);

  // Vazio = "usar o default" (6 mais pressionados).
  const [selectedGroups, setSelectedGroups] = useState<string[]>([]);
  const selectedValidGroups = useMemo(
    () => keepAvailableGroups(selectedGroups, groupOptions),
    [selectedGroups, groupOptions],
  );
  const visibleGroups = useMemo(
    () => resolveVisibleGroups(selectedValidGroups, groupOptions),
    [selectedValidGroups, groupOptions],
  );
  const groupSelectData = useMemo(
    () => groupOptions.map((group) => ({ value: group, label: group })),
    [groupOptions],
  );

  const periods = useMemo(() => buildTrendPeriods(trendRows), [trendRows]);
  const valueIndex = useMemo(() => trendValueIndex(trendRows), [trendRows]);
  const chartPoints = useMemo(
    () => pivotTrend(periods, valueIndex, visibleGroups),
    [periods, valueIndex, visibleGroups],
  );
  const ranking = useMemo(
    () => buildPressureRows(periods, valueIndex, totals, visibleGroups),
    [periods, valueIndex, totals, visibleGroups],
  );

  return (
    <Paper
      withBorder
      radius="lg"
      p="md"
      style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}
      aria-label="Tendência por grupo muscular"
    >
      <Title order={4} c={BRAND.textPrimary}>
        Tendência por grupo muscular
      </Title>
      <Text size="sm" c={BRAND.textMuted} mb="sm">
        Agregado por {TREND_PERIOD_DESCRIPTION[period] || 'período'} e grupo muscular canônico. Janela e
        granularidade próprias deste bloco — não seguem o período do topo da página.
      </Text>

      <Group align="flex-end" gap="sm" wrap="wrap" mb="sm">
        <Select
          size="xs"
          label="Granularidade"
          allowDeselect={false}
          value={period}
          onChange={(value) => isTrendPeriod(value) && updateFilters({ period: value })}
          data={TREND_PERIOD_OPTIONS}
          w={150}
        />
        <Select
          size="xs"
          label="Métrica"
          allowDeselect={false}
          value={metric}
          onChange={(value) => isTrendMetric(value) && updateFilters({ metric: value })}
          data={TREND_METRIC_OPTIONS}
          w={150}
        />
        <Select
          size="xs"
          label="Janela"
          allowDeselect={false}
          value={days}
          onChange={(value) => value && updateFilters({ days: value })}
          data={TREND_DAY_OPTIONS}
          w={130}
        />
        <Checkbox
          size="xs"
          label="Incluir grupos secundários"
          description="Soma também nos grupos secundários e estabilizadores, com o peso cadastrado em cada exercício"
          checked={includeSecondary}
          onChange={(event) => updateFilters({ includeSecondary: event.currentTarget.checked })}
        />
      </Group>

      <Group align="flex-end" justify="space-between" gap="sm" wrap="wrap" mb="md">
        <MultiSelect
          size="xs"
          label="Grupos exibidos"
          description="Por padrão, mostra os seis grupos com maior pressão acumulada."
          data={groupSelectData}
          value={selectedValidGroups}
          onChange={setSelectedGroups}
          searchable
          clearable
          hidePickedOptions
          nothingFoundMessage="Nenhum grupo encontrado"
          w={{ base: '100%', md: 480 }}
        />
        <Button
          size="xs"
          variant="light"
          onClick={() => setSelectedGroups(groupOptions.slice(0, DEFAULT_VISIBLE_GROUPS))}
        >
          Mostrar 6 mais pressionados
        </Button>
      </Group>

      {trendQuery.isPending && <DashboardSkeleton rows={6} aria-label="Carregando tendência por grupo" />}
      {trendQuery.isError && <ErrorState error={trendQuery.error} />}
      {trendQuery.isSuccess && chartPoints.length === 0 && (
        <EmptyState description="Nenhum dado disponível para os filtros selecionados." />
      )}

      {chartPoints.length > 0 && (
        <>
          <MuscleGroupTrendChart chartPoints={chartPoints} visibleGroups={visibleGroups} metric={metric} />

          <Group justify="space-between" mt="lg" mb="xs" wrap="wrap">
            <div>
              <Text fw={700} c={BRAND.textPrimary}>
                Pressão no período mais recente
              </Text>
              <Text size="sm" c={BRAND.textMuted}>
                Ranking dos grupos exibidos e variação contra o período anterior.
              </Text>
            </div>
            <Badge variant="light" color="brand">
              {periods[periods.length - 1] || 'Sem período'}
            </Badge>
          </Group>

          <Table.ScrollContainer minWidth={640}>
            <Table verticalSpacing="sm" highlightOnHover>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Posição</Table.Th>
                  <Table.Th>Grupo</Table.Th>
                  <Table.Th>Período atual</Table.Th>
                  <Table.Th>Período anterior</Table.Th>
                  <Table.Th>Variação</Table.Th>
                  <Table.Th>Total da janela</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {ranking.map((row, index) => (
                  <Table.Tr key={row.group}>
                    <Table.Td>{index + 1}</Table.Td>
                    <Table.Td>
                      <Text fw={600}>{row.group}</Text>
                    </Table.Td>
                    <Table.Td>{formatTrendMetric(row.latest, metric)}</Table.Td>
                    <Table.Td>{formatTrendMetric(row.previous, metric)}</Table.Td>
                    <Table.Td>
                      {row.variation == null ? (
                        <Badge color="gray" variant="light">
                          Sem base
                        </Badge>
                      ) : (
                        <Badge
                          color={row.variation > 5 ? 'red' : row.variation < -5 ? 'teal' : 'gray'}
                          variant="light"
                        >
                          {row.variation > 0 ? '+' : ''}
                          {row.variation.toFixed(1)}%
                        </Badge>
                      )}
                    </Table.Td>
                    <Table.Td>{formatTrendMetric(row.total, metric)}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        </>
      )}
    </Paper>
  );
}
