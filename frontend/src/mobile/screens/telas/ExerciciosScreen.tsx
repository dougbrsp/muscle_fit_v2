'use client';

import { Button, TextInput } from '@mantine/core';
import { IconSearch } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { CartesianGrid, Line, LineChart, XAxis, YAxis } from 'recharts';
import { apiFetch } from '@/lib/api';
import type { ExerciseProgressResponse } from '@/lib/analytics-types';
import { formatDateOrDash, formatFixed, formatKgFixed } from '@/lib/format';
import { CHART_COLORS, CHART_GRID_STROKE } from '@/components/charts/chartTheme';
import {
  BottomSheet,
  ListGroup,
  ListRow,
  MOBILE_CHART_AXIS,
  MOBILE_CHART_MARGIN,
  MobileChart,
  ResponsiveDataList,
  ScreenHeader,
} from '../../ui';

type Item = ExerciseProgressResponse['items'][number];

/**
 * Exercícios (celular): progressão por exercício. Mesma query/chave do desktop
 * (`['analytics-exercise-progress', nome]`); linhas e gráficos mostram os
 * valores prontos da API, sem cálculo.
 */
export function ExerciciosScreen() {
  const [search, setSearch] = useState('Puxada');
  const [query, setQuery] = useState('Puxada');
  const [selected, setSelected] = useState<Item | null>(null);

  const progressQuery = useQuery({
    queryKey: ['analytics-exercise-progress', query],
    queryFn: () => apiFetch<ExerciseProgressResponse>(`/analytics/exercise-progress?name=${encodeURIComponent(query)}&days=1095`),
    enabled: query.trim().length > 0,
  });
  const items = progressQuery.data?.items;

  const weightData = useMemo(
    () =>
      (items ?? []).map((item) => ({
        data: item.local_date,
        melhorCarga: item.best_weight_kg != null ? Number(item.best_weight_kg) : null,
        volume: item.volume_kg != null ? Number(item.volume_kg) : null,
      })),
    [items],
  );
  const rpeData = useMemo(
    () => (items ?? []).map((item) => ({ data: item.local_date, rpeMedio: item.avg_rpe != null ? Number(item.avg_rpe) : null })),
    [items],
  );

  function handleSearch() {
    setQuery(search.trim());
  }

  return (
    <>
      <ScreenHeader title="Exercícios" subtitle="Progressão de carga, volume e RPE" />
      <form
        onSubmit={(e) => {
          e.preventDefault();
          handleSearch();
        }}
        style={{ display: 'flex', gap: 8, marginBottom: 16, alignItems: 'flex-end' }}
      >
        <TextInput
          style={{ flex: 1 }}
          size="md"
          label="Buscar exercício"
          value={search}
          onChange={(e) => setSearch(e.currentTarget.value)}
          placeholder="Ex.: Puxada alta, Leg press"
          enterKeyHint="search"
        />
        <Button type="submit" color="brand" size="md" mih={44} leftSection={<IconSearch size={16} />}>
          Buscar
        </Button>
      </form>

      {items && items.length > 0 ? (
        <>
          <MobileChart label="Melhor carga e volume em kg por data" summary={`Melhor carga e volume (kg) · ${items.length} registros`}>
            <LineChart data={weightData} margin={MOBILE_CHART_MARGIN}>
              <CartesianGrid stroke={CHART_GRID_STROKE} strokeDasharray="4 4" vertical={false} />
              <XAxis dataKey="data" {...MOBILE_CHART_AXIS} />
              <YAxis {...MOBILE_CHART_AXIS} width={44} />
              <Line type="monotone" dataKey="melhorCarga" stroke={CHART_COLORS.primary} strokeWidth={2} dot={false} connectNulls />
              <Line type="monotone" dataKey="volume" stroke={CHART_COLORS.compare} strokeWidth={2} dot={false} connectNulls />
            </LineChart>
          </MobileChart>
          <MobileChart label="RPE médio por data" summary="RPE médio">
            <LineChart data={rpeData} margin={MOBILE_CHART_MARGIN}>
              <CartesianGrid stroke={CHART_GRID_STROKE} strokeDasharray="4 4" vertical={false} />
              <XAxis dataKey="data" {...MOBILE_CHART_AXIS} />
              <YAxis {...MOBILE_CHART_AXIS} width={32} domain={[0, 10]} />
              <Line type="monotone" dataKey="rpeMedio" stroke={CHART_COLORS.secondary} strokeWidth={2} dot={false} connectNulls />
            </LineChart>
          </MobileChart>
        </>
      ) : null}

      <ResponsiveDataList
        aria-label="Progressão do exercício"
        items={items}
        getKey={(item) => `${item.local_date}-${item.original_name}-${item.activity_name}-${item.best_weight_kg}-${item.volume_kg}`}
        isLoading={progressQuery.isLoading}
        isError={progressQuery.isError}
        onRetry={() => void progressQuery.refetch()}
        errorTitle="Erro ao carregar progressão do exercício"
        emptyTitle="Nenhum registro encontrado para essa busca"
        renderItem={(item) => (
          <ListRow
            title={item.mapped_name || item.original_name}
            subtitle={`${formatDateOrDash(item.local_date)} · ${item.activity_name}`}
            value={formatKgFixed(item.best_weight_kg, 1)}
            onClick={() => setSelected(item)}
          />
        )}
      />

      <BottomSheet opened={selected !== null} onClose={() => setSelected(null)} title={selected?.mapped_name || selected?.original_name || 'Exercício'}>
        {selected ? (
          <ListGroup>
            <ListRow title="Data" value={formatDateOrDash(selected.local_date)} />
            <ListRow title="Atividade" value={selected.activity_name} />
            <ListRow title="Séries" value={String(selected.sets_count ?? '-')} />
            <ListRow title="Reps" value={String(selected.reps_total ?? '-')} />
            <ListRow title="Melhor carga" value={formatKgFixed(selected.best_weight_kg, 1)} />
            <ListRow title="Volume" value={formatKgFixed(selected.volume_kg, 0)} />
            <ListRow title="RPE médio" value={selected.avg_rpe != null ? formatFixed(selected.avg_rpe, 1) : '-'} />
            <ListRow title="Séries com RPE alto" value={String(selected.high_rpe_sets ?? 0)} />
          </ListGroup>
        ) : null}
      </BottomSheet>
    </>
  );
}
