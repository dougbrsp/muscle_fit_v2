'use client';

import dynamic from 'next/dynamic';
import { ChartPlaceholder } from '@/components/charts/ChartPlaceholder';
import { Alert, Badge, Grid, Group, Loader, Paper, Select, SimpleGrid, Stack, Table, Text, Title } from '@mantine/core';
import { IconAlertTriangle } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import Link from 'next/link';
import { TablePager } from '@/components/TablePager';
import { apiFetch, ApiError } from '@/lib/api';
import { usePagination } from '@/lib/pagination';
import { formatDateBR, formatNumberOrDash } from '@/lib/physical-indicators-types';
import { averageOf, sumBy, type IntervalsDashboard, type IntervalsStatus } from '@/lib/intervals-types';
import { BRAND } from '@/theme';
import { WellnessManualCell } from './WellnessManualCell';

// COD-14: recharts sob demanda; o placeholder tem a altura do gráfico.
const MinutesChart = dynamic(() => import('./IntervalsIcuCharts').then((m) => m.MinutesChart), {
  ssr: false,
  loading: () => <ChartPlaceholder height={240} />,
});
const LoadChart = dynamic(() => import('./IntervalsIcuCharts').then((m) => m.LoadChart), {
  ssr: false,
  loading: () => <ChartPlaceholder height={240} />,
});
const WellnessChart = dynamic(() => import('./IntervalsIcuCharts').then((m) => m.WellnessChart), {
  ssr: false,
  loading: () => <ChartPlaceholder height={240} />,
});
const RampRateChart = dynamic(() => import('./IntervalsIcuCharts').then((m) => m.RampRateChart), {
  ssr: false,
  loading: () => <ChartPlaceholder height={240} />,
});

const dayOptions = [
  { value: '30', label: '30 dias' },
  { value: '90', label: '90 dias' },
  { value: '180', label: '180 dias' },
  { value: '365', label: '12 meses' },
  { value: '1095', label: '3 anos' },
];

// Melhoria M2: esta tela e de LEITURA (exceto a edicao manual de FC de
// repouso / HRV na tabela de wellness, `WellnessManualCell`). Os botoes "Atualizar" e
// "Sincronizar" sairam - quem grava o cache do Intervals.icu e associa a FC
// aos treinos realizados e o job agendado `musclefit-intervals-sync`
// (EventBridge -> Lambda, ver api/app/intervals_sync.py), que roda sozinho
// para todo usuario com a API do Intervals configurada.
export default function IntervalsIcuPage() {
  const [days, setDays] = useState('90');

  const statusQuery = useQuery({
    queryKey: ['intervals-icu-status'],
    queryFn: () => apiFetch<IntervalsStatus>('/integrations/intervals/status'),
  });
  const configured = statusQuery.data?.configured === true;

  const dashboardQuery = useQuery({
    queryKey: ['intervals-icu-dashboard', days],
    queryFn: () => apiFetch<IntervalsDashboard>(`/integrations/intervals/dashboard?days=${days}`),
    enabled: configured,
  });

  const data = dashboardQuery.data;
  // Memo pelo payload: `?? []` criaria uma lista nova a cada render e
  // invalidaria os useMemo abaixo (gráficos e tabelas paginadas).
  const daily = useMemo(() => data?.daily_activity ?? [], [data]);
  const wellnessTrend = useMemo(() => data?.wellness_trend ?? [], [data]);
  const recent = useMemo(() => data?.recent_activities ?? [], [data]);

  const totalMinutes = sumBy(daily, (item) => item.minutes);
  const totalCalories = sumBy(daily, (item) => item.calories);
  const totalDistance = sumBy(daily, (item) => item.distanceKm);
  const totalLoad = sumBy(daily, (item) => item.load);
  const avgHr = averageOf(daily.map((item) => item.avgHr));

  // Referência local (IntervalsClient.tsx) usa dual axis (minutos/calorias x
  // carga; fitness/fadiga/forma x ramp rate) — quebra a regra de eixo único
  // do dataviz, então aqui viram gráficos separados por unidade compatível.
  const minutesChartData = useMemo(
    () => daily.map((item) => ({ data: formatDateBR(item.date), minutos: item.minutes })),
    [daily],
  );
  const loadChartData = useMemo(
    () => daily.map((item) => ({ data: formatDateBR(item.date), carga: item.load })),
    [daily],
  );
  const wellnessChartData = useMemo(
    () =>
      wellnessTrend.map((item) => ({
        data: formatDateBR(item.date),
        fitness: item.fitness,
        fatigue: item.fatigue,
        form: item.form,
      })),
    [wellnessTrend],
  );
  const rampRateChartData = useMemo(
    () => wellnessTrend.map((item) => ({ data: formatDateBR(item.date), rampa: item.rampRate })),
    [wellnessTrend],
  );

  // Tabelas: mais recente primeiro, paginadas no cliente. Os hooks ficam aqui,
  // antes dos returns antecipados; trocar a janela volta para a página 1.
  const dailyRows = useMemo(() => [...daily].reverse(), [daily]);
  const wellnessRows = useMemo(() => [...wellnessTrend].reverse(), [wellnessTrend]);
  // Sem id na atividade: a key leva o índice ABSOLUTO na lista (não o da página).
  const recentRows = useMemo(
    () => recent.map((item, index) => ({ item, key: `${item.date}-${item.name}-${index}` })),
    [recent],
  );
  const dailyPagination = usePagination(dailyRows, { resetKey: days });
  const wellnessPagination = usePagination(wellnessRows, { resetKey: days });
  const recentPagination = usePagination(recentRows, { resetKey: days });

  if (statusQuery.isSuccess && !configured) {
    return (
      <Stack gap="xl">
        <div>
          <Title order={1} c={BRAND.textPrimary}>
            Intervals.icu
          </Title>
          <Text c={BRAND.textMuted} size="md">
            Dashboard de wellness/atividades. A sincronização é automática.
          </Text>
        </div>
        <Alert color="blue" icon={<IconAlertTriangle />} title="Integração não configurada">
          Configure sua chave do Intervals.icu no seu perfil para usar esta tela.{' '}
          <Text component={Link} href="/perfil" fw={700} td="underline" c="brand">
            Ir para o perfil
          </Text>
        </Alert>
      </Stack>
    );
  }

  return (
    <Stack gap="xl">
      <Group justify="space-between" align="flex-end" wrap="wrap">
        <div>
          <Title order={1} c={BRAND.textPrimary}>
            Intervals.icu
          </Title>
          <Text c={BRAND.textMuted} size="md">
            Dashboard de wellness/atividades. Atividades vêm ao vivo do Intervals.icu; o wellness (fitness,
            fadiga, forma, FC de repouso e HRV) vem do mesmo histórico salvo usado em Indicadores físicos, atualizado
            ao abrir esta página e diariamente de forma automática.
          </Text>
        </div>

        <Group align="flex-end">
          <Select label="Janela" value={days} onChange={(value) => setDays(value || '90')} data={dayOptions} w={150} />
        </Group>
      </Group>

      {statusQuery.isLoading && (
        <Group>
          <Loader size="sm" color="brand" />
          <Text c={BRAND.textMuted}>Verificando status da integração...</Text>
        </Group>
      )}

      {statusQuery.isError && (
        <Alert color="red" icon={<IconAlertTriangle />} title="Erro ao verificar status">
          {statusQuery.error instanceof ApiError ? statusQuery.error.message : 'Não foi possível consultar a Fitness API.'}
        </Alert>
      )}

      {dashboardQuery.isLoading && (
        <Group>
          <Loader size="sm" color="brand" />
          <Text c={BRAND.textMuted}>Carregando dashboard do Intervals.icu...</Text>
        </Group>
      )}

      {dashboardQuery.isError && (
        <Alert color="red" icon={<IconAlertTriangle />} title="Erro ao carregar dashboard">
          {dashboardQuery.error instanceof ApiError ? dashboardQuery.error.message : 'Não foi possível consultar a Fitness API.'}
        </Alert>
      )}

      {!dashboardQuery.isLoading && !dashboardQuery.isError && data && (
        <>
          <SimpleGrid cols={{ base: 2, sm: 4 }} spacing="md">
            <Paper withBorder radius="lg" p="md" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
              <Stack gap={4}>
                <Text size="xs" c={BRAND.textMuted} tt="uppercase" fw={600}>
                  Atividades
                </Text>
                <Text size="xl" fw={700} c={BRAND.textPrimary}>
                  {data.activity_count}
                </Text>
              </Stack>
            </Paper>
            <Paper withBorder radius="lg" p="md" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
              <Stack gap={4}>
                <Text size="xs" c={BRAND.textMuted} tt="uppercase" fw={600}>
                  Tempo total
                </Text>
                <Text size="xl" fw={700} c={BRAND.textPrimary}>
                  {totalMinutes.toLocaleString('pt-BR')} min
                </Text>
              </Stack>
            </Paper>
            <Paper withBorder radius="lg" p="md" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
              <Stack gap={4}>
                <Text size="xs" c={BRAND.textMuted} tt="uppercase" fw={600}>
                  Carga (training load)
                </Text>
                <Text size="xl" fw={700} c={BRAND.textPrimary}>
                  {Math.round(totalLoad).toLocaleString('pt-BR')}
                </Text>
              </Stack>
            </Paper>
            <Paper withBorder radius="lg" p="md" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
              <Stack gap={4}>
                <Text size="xs" c={BRAND.textMuted} tt="uppercase" fw={600}>
                  FC média
                </Text>
                <Text size="xl" fw={700} c={BRAND.textPrimary}>
                  {avgHr ? avgHr.toFixed(0) : '-'}
                </Text>
                <Text size="xs" c={BRAND.textMuted}>
                  {totalDistance.toFixed(1)} km · {Math.round(totalCalories).toLocaleString('pt-BR')} kcal
                </Text>
              </Stack>
            </Paper>
          </SimpleGrid>

          {(minutesChartData.length > 0 || loadChartData.length > 0) && (
            <Grid>
              <Grid.Col span={{ base: 12, md: 6 }}>
                <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
                  <Title order={4} mb="sm" c={BRAND.textPrimary}>
                    Minutos por dia
                  </Title>
                  <MinutesChart minutesChartData={minutesChartData} />
                </Paper>
              </Grid.Col>
              <Grid.Col span={{ base: 12, md: 6 }}>
                <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
                  <Title order={4} mb="sm" c={BRAND.textPrimary}>
                    Carga (training load) por dia
                  </Title>
                  <LoadChart loadChartData={loadChartData} />
                </Paper>
              </Grid.Col>
            </Grid>
          )}

          {(wellnessChartData.length > 0 || rampRateChartData.length > 0) && (
            <Grid>
              <Grid.Col span={{ base: 12, md: 7 }}>
                <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
                  <Title order={4} mb="sm" c={BRAND.textPrimary}>
                    Fitness, fadiga e forma
                  </Title>
                  <WellnessChart wellnessChartData={wellnessChartData} />
                </Paper>
              </Grid.Col>
              <Grid.Col span={{ base: 12, md: 5 }}>
                <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
                  <Title order={4} mb="sm" c={BRAND.textPrimary}>
                    Ramp rate
                  </Title>
                  <RampRateChart rampRateChartData={rampRateChartData} />
                </Paper>
              </Grid.Col>
            </Grid>
          )}

          <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
            <Group justify="space-between" mb="sm">
              <div>
                <Title order={3} c={BRAND.textPrimary}>
                  Atividades por dia
                </Title>
                <Text size="sm" c={BRAND.textMuted}>
                  Minutos, calorias, distância e carga por dia no período selecionado.
                </Text>
              </div>
              <Badge color="brand" variant="light">
                {daily.length} dias
              </Badge>
            </Group>
            <Table.ScrollContainer minWidth={700}>
              <Table striped highlightOnHover withTableBorder verticalSpacing="sm">
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Data</Table.Th>
                    <Table.Th>Atividades</Table.Th>
                    <Table.Th>Minutos</Table.Th>
                    <Table.Th>Calorias</Table.Th>
                    <Table.Th>Distância (km)</Table.Th>
                    <Table.Th>Carga</Table.Th>
                    <Table.Th>FC média</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {dailyPagination.pageItems.map((item) => (
                    <Table.Tr key={item.date}>
                      <Table.Td style={{ whiteSpace: 'nowrap' }}>{formatDateBR(item.date)}</Table.Td>
                      <Table.Td>{item.activities}</Table.Td>
                      <Table.Td>{item.minutes}</Table.Td>
                      <Table.Td>{item.calories || '-'}</Table.Td>
                      <Table.Td>{item.distanceKm.toFixed(2)}</Table.Td>
                      <Table.Td>{formatNumberOrDash(item.load, 1)}</Table.Td>
                      <Table.Td>{item.avgHr != null ? item.avgHr.toFixed(0) : '-'}</Table.Td>
                    </Table.Tr>
                  ))}
                  {daily.length === 0 && (
                    <Table.Tr>
                      <Table.Td colSpan={7}>
                        <Text c={BRAND.textMuted} ta="center" py="md">
                          Nenhuma atividade no período selecionado.
                        </Text>
                      </Table.Td>
                    </Table.Tr>
                  )}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
            <TablePager
              page={dailyPagination.page}
              totalPages={dailyPagination.totalPages}
              onChange={dailyPagination.setPage}
              visible={dailyPagination.showPager}
            />
          </Paper>

          <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
            <Group justify="space-between" mb="sm">
              <div>
                <Title order={3} c={BRAND.textPrimary}>
                  Fitness, fadiga e forma
                </Title>
                <Text size="sm" c={BRAND.textMuted}>
                  Wellness ao vivo do Intervals.icu (CTL/ATL/TSB, HRV, FC de repouso e ramp rate). FC de
                  repouso e HRV podem ser editados; o Intervals sobrescreve se enviar o dado do dia.
                </Text>
              </div>
              <Badge color="brand" variant="light">
                {wellnessTrend.length} dias
              </Badge>
            </Group>
            <Table.ScrollContainer minWidth={820}>
              <Table striped highlightOnHover withTableBorder verticalSpacing="sm">
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Data</Table.Th>
                    <Table.Th>Fitness (CTL)</Table.Th>
                    <Table.Th>Fadiga (ATL)</Table.Th>
                    <Table.Th>Forma (TSB)</Table.Th>
                    <Table.Th>FC repouso</Table.Th>
                    <Table.Th>HRV</Table.Th>
                    <Table.Th>Ramp rate</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {wellnessPagination.pageItems.map((item) => (
                    <Table.Tr key={item.date}>
                      <Table.Td style={{ whiteSpace: 'nowrap' }}>{formatDateBR(item.date)}</Table.Td>
                      <Table.Td>{formatNumberOrDash(item.fitness, 1)}</Table.Td>
                      <Table.Td>{formatNumberOrDash(item.fatigue, 1)}</Table.Td>
                      <Table.Td>{formatNumberOrDash(item.form, 1)}</Table.Td>
                      <Table.Td>
                        <WellnessManualCell
                          date={item.date}
                          field="resting_hr_bpm"
                          label="FC de repouso"
                          value={item.restingHr}
                          source={item.restingHrSource}
                        />
                      </Table.Td>
                      <Table.Td>
                        <WellnessManualCell
                          date={item.date}
                          field="hrv_ms"
                          label="HRV"
                          value={item.hrv}
                          source={item.hrvSource}
                        />
                      </Table.Td>
                      <Table.Td>{formatNumberOrDash(item.rampRate, 1)}</Table.Td>
                    </Table.Tr>
                  ))}
                  {wellnessTrend.length === 0 && (
                    <Table.Tr>
                      <Table.Td colSpan={7}>
                        <Text c={BRAND.textMuted} ta="center" py="md">
                          Nenhum dado de wellness no período selecionado.
                        </Text>
                      </Table.Td>
                    </Table.Tr>
                  )}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
            <TablePager
              page={wellnessPagination.page}
              totalPages={wellnessPagination.totalPages}
              onChange={wellnessPagination.setPage}
              visible={wellnessPagination.showPager}
            />
          </Paper>

          <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
            <Group justify="space-between" mb="sm">
              <div>
                <Title order={3} c={BRAND.textPrimary}>
                  Atividades recentes
                </Title>
                <Text size="sm" c={BRAND.textMuted}>
                  Últimas atividades lidas do Intervals.icu (até 30). A gravação no banco é feita pelo sync
                  automático diário.
                </Text>
              </div>
              <Badge color="brand" variant="light">
                {recent.length} linhas
              </Badge>
            </Group>
            <Table.ScrollContainer minWidth={700}>
              <Table striped highlightOnHover withTableBorder verticalSpacing="sm">
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Data</Table.Th>
                    <Table.Th>Nome</Table.Th>
                    <Table.Th>Tipo</Table.Th>
                    <Table.Th>Minutos</Table.Th>
                    <Table.Th>Calorias</Table.Th>
                    <Table.Th>FC média</Table.Th>
                    <Table.Th>Carga</Table.Th>
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {recentPagination.pageItems.map(({ item, key }) => (
                    <Table.Tr key={key}>
                      <Table.Td style={{ whiteSpace: 'nowrap' }}>{formatDateBR(item.date)}</Table.Td>
                      <Table.Td>{item.name}</Table.Td>
                      <Table.Td>{item.sport}</Table.Td>
                      <Table.Td>{item.minutes}</Table.Td>
                      <Table.Td>{item.calories || '-'}</Table.Td>
                      <Table.Td>{item.avgHr ?? '-'}</Table.Td>
                      <Table.Td>{item.load ?? '-'}</Table.Td>
                    </Table.Tr>
                  ))}
                  {recent.length === 0 && (
                    <Table.Tr>
                      <Table.Td colSpan={7}>
                        <Text c={BRAND.textMuted} ta="center" py="md">
                          Nenhuma atividade recente encontrada.
                        </Text>
                      </Table.Td>
                    </Table.Tr>
                  )}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>
            <TablePager
              page={recentPagination.page}
              totalPages={recentPagination.totalPages}
              onChange={recentPagination.setPage}
              visible={recentPagination.showPager}
            />
          </Paper>
        </>
      )}
    </Stack>
  );
}
