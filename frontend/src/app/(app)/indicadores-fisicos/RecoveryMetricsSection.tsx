'use client';

import dynamic from 'next/dynamic';
import { ChartPlaceholder } from '@/components/charts/ChartPlaceholder';
import { Alert, Badge, Grid, Group, Loader, Paper, Select, SimpleGrid, Stack, Table, Text, Title } from '@mantine/core';
import { IconAlertTriangle, IconInfoCircle } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { TablePager } from '@/components/TablePager';
import { apiFetch, ApiError } from '@/lib/api';
import { usePagination } from '@/lib/pagination';
import {
  formatDateBR,
  formatNumberOrDash,
  IntervalsWellnessResponse,
} from '@/lib/physical-indicators-types';
import { BRAND } from '@/theme';

// COD-14: recharts sob demanda; o placeholder tem a altura do gráfico.
const TrainingLoadChart = dynamic(() => import('./RecoveryMetricsCharts').then((m) => m.TrainingLoadChart), {
  ssr: false,
  loading: () => <ChartPlaceholder height={260} />,
});
const HrvChart = dynamic(() => import('./RecoveryMetricsCharts').then((m) => m.HrvChart), {
  ssr: false,
  loading: () => <ChartPlaceholder height={260} />,
});

// Só leitura: série cacheada, sincronizada externamente do Intervals.icu —
// sem CRUD. Ver `GET /integrations/intervals/wellness` em
// `C:\muscle_fit\api\app\intervals_integration.py`.
const CARDS = [
  { key: 'fitness_ctl', label: 'Aptidão (CTL)', digits: 1 },
  { key: 'fatigue_atl', label: 'Fadiga (ATL)', digits: 1 },
  { key: 'form_tsb', label: 'Forma (TSB)', digits: 1 },
  { key: 'ramp_rate', label: 'Rampa', digits: 1 },
  { key: 'readiness_score', label: 'Preparo', digits: 0 },
  { key: 'daily_effort_load', label: 'Carga diária de esforço', digits: 0 },
  { key: 'hrv_ms', label: 'HRV (ms)', digits: 0 },
] as const;

// Mesmo padrão visual do seletor de período usado em /evolucao-corporal.
const DAY_OPTIONS = [
  { value: '30', label: '30 dias' },
  { value: '90', label: '90 dias' },
  { value: '180', label: '6 meses' },
  { value: '365', label: '1 ano' },
];

export function RecoveryMetricsSection() {
  const [days, setDays] = useState('90');
  // GET /integrations/intervals/wellness aceita days em [1,3650] e limit em
  // [1,1000] (ver api/app/intervals_integration.py) — days*2 cobre o pior
  // caso (um registro por dia) sem estourar o teto de 1000.
  const limit = Math.min(Number(days) * 2, 1000);

  const query = useQuery({
    queryKey: ['intervals-wellness', days],
    queryFn: () =>
      apiFetch<IntervalsWellnessResponse>(`/integrations/intervals/wellness?days=${days}&limit=${limit}`),
  });

  const items = query.data?.items || [];
  // Só a tabela é paginada; gráficos e badge usam a lista completa.
  const pagination = usePagination(items, { resetKey: days });
  const latest = items[0];

  // API retorna mais recente primeiro; gráfico quer ordem cronológica.
  const chartData = useMemo(
    () =>
      [...items]
        .reverse()
        .map((item) => ({
          data: formatDateBR(item.wellness_date),
          fitness_ctl: item.fitness_ctl != null ? Number(item.fitness_ctl) : null,
          fatigue_atl: item.fatigue_atl != null ? Number(item.fatigue_atl) : null,
          form_tsb: item.form_tsb != null ? Number(item.form_tsb) : null,
          hrv_ms: item.hrv_ms != null ? Number(item.hrv_ms) : null,
        })),
    [items],
  );

  return (
    <Stack gap="md">
      <Group justify="space-between" align="flex-end">
        <div>
          <Title order={2} c={BRAND.textPrimary}>
            Recuperação (Intervals.icu)
          </Title>
          <Text c={BRAND.textMuted} size="sm">
            Série sincronizada automaticamente do Intervals.icu — somente leitura.
          </Text>
        </div>
        <Select label="Período" value={days} onChange={(value) => setDays(value || '90')} data={DAY_OPTIONS} w={160} />
      </Group>

      {query.isLoading && (
        <Group>
          <Loader size="sm" color="brand" />
          <Text c={BRAND.textMuted}>Carregando dados do Intervals.icu...</Text>
        </Group>
      )}

      {query.isError && (
        <Alert color="red" icon={<IconAlertTriangle />} title="Erro ao carregar recuperação">
          {query.error instanceof ApiError ? query.error.message : 'Não foi possível consultar a Fitness API.'}
        </Alert>
      )}

      {!query.isLoading && !query.isError && (
        <SimpleGrid cols={{ base: 2, sm: 3, md: 4, lg: 7 }} spacing="md">
          {CARDS.map((card) => (
            <Paper
              key={card.key}
              withBorder
              radius="lg"
              p="md"
              style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}
            >
              <Stack gap={4}>
                <Text size="xs" c={BRAND.textMuted} tt="uppercase" fw={600}>
                  {card.label}
                </Text>
                <Text size="xl" fw={700} c={BRAND.textPrimary}>
                  {formatNumberOrDash(latest?.[card.key], card.digits)}
                </Text>
              </Stack>
            </Paper>
          ))}
        </SimpleGrid>
      )}

      {!query.isLoading && !query.isError && chartData.length > 0 && (
        <Grid>
          <Grid.Col span={{ base: 12, md: 6 }}>
            <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
              <Title order={4} mb="sm" c={BRAND.textPrimary}>
                Aptidão, fadiga e forma (CTL/ATL/TSB)
              </Title>
              <TrainingLoadChart chartData={chartData} />
            </Paper>
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 6 }}>
            <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
              <Title order={4} mb="sm" c={BRAND.textPrimary}>
                HRV (variabilidade da frequência cardíaca)
              </Title>
              <HrvChart chartData={chartData} />
            </Paper>
          </Grid.Col>
        </Grid>
      )}

      {!query.isLoading && !query.isError && (
        <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
          <Group justify="space-between" mb="sm">
            <Title order={4} c={BRAND.textPrimary}>
              Histórico ({DAY_OPTIONS.find((o) => o.value === days)?.label ?? `${days} dias`})
            </Title>
            <Badge color="brand" variant="light">
              {items.length} dias sincronizados
            </Badge>
          </Group>
          <Table.ScrollContainer minWidth={800}>
            <Table striped highlightOnHover withTableBorder verticalSpacing="sm">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Data</Table.Th>
                  <Table.Th>CTL</Table.Th>
                  <Table.Th>ATL</Table.Th>
                  <Table.Th>TSB</Table.Th>
                  <Table.Th>Rampa</Table.Th>
                  <Table.Th>Preparo</Table.Th>
                  <Table.Th>Carga diária</Table.Th>
                  <Table.Th>HRV</Table.Th>
                  <Table.Th>FC repouso</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {pagination.pageItems.map((entry) => (
                  <Table.Tr key={entry.wellness_date}>
                    <Table.Td style={{ whiteSpace: 'nowrap' }}>{formatDateBR(entry.wellness_date)}</Table.Td>
                    <Table.Td>{formatNumberOrDash(entry.fitness_ctl)}</Table.Td>
                    <Table.Td>{formatNumberOrDash(entry.fatigue_atl)}</Table.Td>
                    <Table.Td>{formatNumberOrDash(entry.form_tsb)}</Table.Td>
                    <Table.Td>{formatNumberOrDash(entry.ramp_rate)}</Table.Td>
                    <Table.Td>{formatNumberOrDash(entry.readiness_score, 0)}</Table.Td>
                    <Table.Td>{formatNumberOrDash(entry.daily_effort_load, 0)}</Table.Td>
                    <Table.Td>{formatNumberOrDash(entry.hrv_ms, 0)}</Table.Td>
                    <Table.Td>{formatNumberOrDash(entry.resting_hr_bpm, 0)}</Table.Td>
                  </Table.Tr>
                ))}
                {items.length === 0 && (
                  <Table.Tr>
                    <Table.Td colSpan={9}>
                      <Text c={BRAND.textMuted} ta="center" py="md">
                        Nenhum dado sincronizado do Intervals.icu ainda.
                      </Text>
                    </Table.Td>
                  </Table.Tr>
                )}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
          <TablePager
            page={pagination.page}
            totalPages={pagination.totalPages}
            onChange={pagination.setPage}
            visible={pagination.showPager}
          />
        </Paper>
      )}

      <Group gap={6} align="flex-start">
        <IconInfoCircle size={16} color={BRAND.textMuted} style={{ marginTop: 2, flexShrink: 0 }} />
        <Text size="xs" c={BRAND.textMuted}>
          &quot;Preparo&quot; e &quot;HRV&quot; dependem do Intervals.icu fornecer esses campos — podem aparecer como &quot;-&quot; se a conta ou o
          dispositivo não os popular. Sem CRUD manual: esta seção é somente leitura.
        </Text>
      </Group>
    </Stack>
  );
}
