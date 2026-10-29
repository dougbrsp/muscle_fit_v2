'use client';

import dynamic from 'next/dynamic';
import { ChartPlaceholder } from '@/components/charts/ChartPlaceholder';
import { Alert, Button, Grid, Group, Paper, Stack, Table, Text, TextInput, Title } from '@mantine/core';
import { IconAlertTriangle, IconSearch } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { apiFetch } from '@/lib/api';
import type { ExerciseProgressResponse } from '@/lib/analytics-types';
import { BRAND } from '@/theme';
import { formatFixed, formatKgFixed } from '@/lib/format';

// COD-14: recharts sob demanda; o placeholder tem a altura do gráfico.
const WeightChart = dynamic(() => import('./ExerciciosCharts').then((m) => m.WeightChart), {
  ssr: false,
  loading: () => <ChartPlaceholder height={280} />,
});
const RpeChart = dynamic(() => import('./ExerciciosCharts').then((m) => m.RpeChart), {
  ssr: false,
  loading: () => <ChartPlaceholder height={280} />,
});

export default function ExerciciosPage() {
  const [search, setSearch] = useState('Puxada');
  const [query, setQuery] = useState('Puxada');

  const progressQuery = useQuery({
    queryKey: ['analytics-exercise-progress', query],
    queryFn: () => apiFetch<ExerciseProgressResponse>(`/analytics/exercise-progress?name=${encodeURIComponent(query)}&days=1095`),
    enabled: query.trim().length > 0,
  });

  const items = progressQuery.data?.items || [];

  function handleSearch() {
    setQuery(search.trim());
  }

  // A referência local (ExercisesClient.tsx) plota melhor carga/volume (kg)
  // e RPE (escala 0-10) num mesmo gráfico com eixo duplo — quebra a regra de
  // eixo único do dataviz aplicada na Etapa 26a, então aqui viram dois
  // gráficos de eixo simples: um em kg, outro em RPE (0-10).
  const weightChartData = useMemo(
    () =>
      items.map((item) => ({
        data: item.local_date,
        melhorCarga: item.best_weight_kg != null ? Number(item.best_weight_kg) : null,
        volume: item.volume_kg != null ? Number(item.volume_kg) : null,
      })),
    [items],
  );

  const rpeChartData = useMemo(
    () =>
      items.map((item) => ({
        data: item.local_date,
        rpeMedio: item.avg_rpe != null ? Number(item.avg_rpe) : null,
      })),
    [items],
  );

  return (
    <Stack gap="xl">
      <Group justify="space-between" align="flex-end">
        <div>
          <Title order={1} c={BRAND.textPrimary}>
            Exercícios
          </Title>
          <Text c={BRAND.textMuted} size="md">
            Progressão de carga, volume e RPE por exercício.
          </Text>
        </div>
      </Group>

      <Group align="end">
        <TextInput
          label="Buscar exercício"
          value={search}
          onChange={(event) => setSearch(event.currentTarget.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') handleSearch();
          }}
          placeholder="Ex.: Puxada alta, Leg press, Remada"
          w={340}
        />
        <Button leftSection={<IconSearch size={16} />} color="brand" onClick={handleSearch}>
          Buscar
        </Button>
      </Group>

      {progressQuery.isLoading && (
        <Group>
          <Text c={BRAND.textMuted}>Carregando dados da Fitness API...</Text>
        </Group>
      )}

      {progressQuery.isError && (
        <Alert color="red" icon={<IconAlertTriangle />} title="Erro ao carregar progressão do exercício">
          {progressQuery.error instanceof Error ? progressQuery.error.message : 'Não foi possível consultar a Fitness API.'}
        </Alert>
      )}

      {!progressQuery.isLoading && !progressQuery.isError && items.length > 0 && (
        <Grid>
          <Grid.Col span={{ base: 12, md: 6 }}>
            <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
              <Title order={4} mb="sm" c={BRAND.textPrimary}>
                Melhor carga e volume (kg)
              </Title>
              <WeightChart weightChartData={weightChartData} />
            </Paper>
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 6 }}>
            <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
              <Title order={4} mb="sm" c={BRAND.textPrimary}>
                RPE médio
              </Title>
              <RpeChart rpeChartData={rpeChartData} />
            </Paper>
          </Grid.Col>
        </Grid>
      )}

      {!progressQuery.isLoading && !progressQuery.isError && (
        <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
          <Title order={3} mb="sm" c={BRAND.textPrimary}>
            Progressão do exercício
          </Title>
          <Table.ScrollContainer minWidth={900}>
            <Table striped highlightOnHover withTableBorder>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Data</Table.Th>
                  <Table.Th>Atividade</Table.Th>
                  <Table.Th>Exercício</Table.Th>
                  <Table.Th>Séries</Table.Th>
                  <Table.Th>Reps</Table.Th>
                  <Table.Th>Melhor carga</Table.Th>
                  <Table.Th>Volume</Table.Th>
                  <Table.Th>RPE médio</Table.Th>
                  <Table.Th>RPE alto</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {items.map((item, index) => (
                  <Table.Tr key={`${item.local_date}-${item.original_name}-${index}`}>
                    <Table.Td>{item.local_date}</Table.Td>
                    <Table.Td>{item.activity_name}</Table.Td>
                    <Table.Td>{item.mapped_name || item.original_name}</Table.Td>
                    <Table.Td>{item.sets_count ?? '-'}</Table.Td>
                    <Table.Td>{item.reps_total ?? '-'}</Table.Td>
                    <Table.Td>{formatKgFixed(item.best_weight_kg, 1)}</Table.Td>
                    <Table.Td>{formatKgFixed(item.volume_kg, 0)}</Table.Td>
                    <Table.Td>{item.avg_rpe != null ? formatFixed(item.avg_rpe, 1) : '-'}</Table.Td>
                    <Table.Td>{item.high_rpe_sets ?? 0}</Table.Td>
                  </Table.Tr>
                ))}
                {items.length === 0 && (
                  <Table.Tr>
                    <Table.Td colSpan={9}>
                      <Text c={BRAND.textMuted} ta="center">
                        Nenhum registro encontrado para essa busca.
                      </Text>
                    </Table.Td>
                  </Table.Tr>
                )}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        </Paper>
      )}
    </Stack>
  );
}
