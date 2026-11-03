'use client';

import { Button, Select, Stack, Text, TextInput } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { IconPlus } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useRouter } from 'next/navigation';
import { useMemo, useState } from 'react';
import { NewWorkoutModal } from '@/components/NewWorkoutModal';
import { apiFetch } from '@/lib/api';
import { formatNumberOrDash } from '@/lib/format';
import {
  ApiList,
  buildStartedAt,
  conductedByOptions,
  GymResponse,
  periodToDateFrom,
  sessionStatusLabels,
  WorkoutHeaderForm,
  WorkoutListItem,
} from '@/lib/workout-log-types';
import {
  BottomSheet,
  FilterButton,
  FilterSheet,
  ListGroup,
  ListRow,
  ResponsiveDataList,
  ScreenHeader,
  SegmentedFilter,
  StickyAction,
} from '../../ui';
import { SheetButton } from './common';
import { PortalFieldsStyle } from './PortalFieldsStyle';

const PAGE_SIZE = 20;
// Mesmo lote do desktop: o backend filtra e a paginação é no cliente.
const FETCH_LIMIT = 200;

const periodOptions = [
  { value: 'all', label: 'Todo período' },
  { value: '7', label: 'Últimos 7 dias' },
  { value: '14', label: 'Últimos 14 dias' },
  { value: '30', label: 'Últimos 30 dias' },
  { value: '90', label: 'Últimos 90 dias' },
];
const quickPeriods = [
  { value: 'all', label: 'Tudo' },
  { value: '7', label: '7 dias' },
  { value: '30', label: '30 dias' },
  { value: '90', label: '90 dias' },
];

type StartSessionResult = { session_id: number };

/** Histórico mobile: mesma query/chaves do desktop (`['activities', ...]`). */
export function HistoricoScreen() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [searchText, setSearchText] = useState('');
  const [periodDays, setPeriodDays] = useState('all');
  const [gymFilter, setGymFilter] = useState('');
  const [conductedByFilter, setConductedByFilter] = useState('');
  const [visible, setVisible] = useState(PAGE_SIZE);
  const [filtersOpen, { open: openFilters, close: closeFilters }] = useDisclosure(false);
  const [newOpen, { open: openNew, close: closeNew }] = useDisclosure(false);
  const [selected, setSelected] = useState<WorkoutListItem | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const gymsQuery = useQuery({
    queryKey: ['gyms', 'active'],
    queryFn: () => apiFetch<GymResponse>('/gyms?active_only=true'),
  });
  const gymOptions = useMemo(
    () => (gymsQuery.data?.items || []).map((g) => ({ value: String(g.id), label: g.name })),
    [gymsQuery.data],
  );

  const dateFrom = periodToDateFrom(periodDays);
  const listQuery = useQuery({
    queryKey: ['activities', { searchText, dateFrom, gymFilter, conductedByFilter }],
    queryFn: () => {
      const params = new URLSearchParams({ limit: String(FETCH_LIMIT) });
      if (searchText.trim()) params.set('q', searchText.trim());
      if (dateFrom) params.set('date_from', dateFrom);
      if (gymFilter) params.set('gym_id', gymFilter);
      if (conductedByFilter) params.set('conducted_by', conductedByFilter);
      return apiFetch<ApiList<WorkoutListItem>>(`/activities?${params.toString()}`);
    },
  });

  const startMutation = useMutation({
    mutationFn: async (form: WorkoutHeaderForm) =>
      apiFetch<StartSessionResult>('/workout-sessions/start', {
        method: 'POST',
        body: JSON.stringify({
          name: form.name,
          source_name: 'registro-manual',
          local_date: form.local_date,
          started_at: buildStartedAt(form.local_date, form.started_at_time),
          gym_id: form.gym_id ? Number(form.gym_id) : null,
          training_type_id: form.training_type_id ? Number(form.training_type_id) : null,
          conducted_by: form.conducted_by || null,
          workout_plan_id: form.workout_plan_id ? Number(form.workout_plan_id) : null,
        }),
      }),
    onSuccess: async (result) => {
      closeNew();
      await queryClient.invalidateQueries({ queryKey: ['activities'] });
      router.push(`/registro-exercicios/${result.session_id}`);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: async (activityId: number) => apiFetch(`/activities/${activityId}`, { method: 'DELETE' }),
    onSuccess: async () => {
      setConfirmDelete(false);
      setSelected(null);
      await queryClient.invalidateQueries({ queryKey: ['activities'] });
    },
  });

  const items = listQuery.data?.items;
  const shown = items?.slice(0, visible);
  const activeFilters = (periodDays !== 'all' ? 1 : 0) + (gymFilter ? 1 : 0) + (conductedByFilter ? 1 : 0);

  function withPageReset<T>(setter: (v: T) => void) {
    return (value: T) => {
      setVisible(PAGE_SIZE);
      setter(value);
    };
  }

  function closeSheet() {
    setSelected(null);
    setConfirmDelete(false);
    deleteMutation.reset();
  }

  const statusInfo = selected?.session_status ? sessionStatusLabels[selected.session_status] : null;
  const conductor = selected
    ? (conductedByOptions.find((o) => o.value === selected.conducted_by)?.label ?? selected.conducted_by)
    : null;

  return (
    <>
      <ScreenHeader
        title="Histórico"
        subtitle={items ? `${items.length} treinos` : 'Treinos lançados'}
        actions={<FilterButton activeCount={activeFilters} onClick={openFilters} />}
      />
      <Stack gap="sm" mb="md">
        <TextInput
          aria-label="Buscar treino"
          placeholder="Buscar treino pelo nome"
          value={searchText}
          onChange={(e) => withPageReset(setSearchText)(e.currentTarget.value)}
          size="md"
        />
        <SegmentedFilter label="Período" value={periodDays} onChange={withPageReset(setPeriodDays)} options={quickPeriods} />
      </Stack>

      <ResponsiveDataList
        aria-label="Treinos lançados"
        items={shown}
        getKey={(w) => w.id}
        isLoading={listQuery.isLoading}
        isError={listQuery.isError}
        onRetry={() => void listQuery.refetch()}
        errorTitle="Erro ao carregar treinos"
        emptyTitle="Nenhum treino encontrado com os filtros atuais"
        renderItem={(w) => {
          const st = w.session_status ? sessionStatusLabels[w.session_status] : null;
          return (
            <ListRow
              title={w.name}
              subtitle={[w.local_date, st?.label, w.workout_plan_name].filter(Boolean).join(' · ')}
              value={`${formatNumberOrDash(w.total_volume_kg, 1)} kg`}
              onClick={() => setSelected(w)}
            />
          );
        }}
      />
      {items && shown && items.length > shown.length ? (
        <Button variant="default" fullWidth mt="md" mih={48} onClick={() => setVisible((v) => v + PAGE_SIZE)}>
          Carregar mais ({items.length - shown.length})
        </Button>
      ) : null}

      <StickyAction>
        <Button fullWidth size="lg" color="brand" mih={48} leftSection={<IconPlus size={20} />} onClick={openNew}>
          Novo treino
        </Button>
      </StickyAction>

      <FilterSheet
        opened={filtersOpen}
        onClose={closeFilters}
        activeCount={activeFilters}
        onClear={() => {
          setVisible(PAGE_SIZE);
          setPeriodDays('all');
          setGymFilter('');
          setConductedByFilter('');
        }}
      >
        <Select
          label="Período"
          value={periodDays}
          onChange={(v) => withPageReset(setPeriodDays)(v || 'all')}
          data={periodOptions}
          allowDeselect={false}
        />
        <Select
          searchable
          label="Academia"
          placeholder="Todas"
          value={gymFilter || null}
          onChange={(v) => withPageReset(setGymFilter)(v || '')}
          data={gymOptions}
          clearable
        />
        <Select
          label="Condutor"
          placeholder="Todos"
          value={conductedByFilter || null}
          onChange={(v) => withPageReset(setConductedByFilter)(v || '')}
          data={conductedByOptions}
          clearable
        />
      </FilterSheet>

      <BottomSheet
        opened={selected !== null}
        onClose={closeSheet}
        title={confirmDelete ? 'Confirmar exclusão' : (selected?.name ?? 'Treino')}
        footer={
          selected ? (
            confirmDelete ? (
              <>
                <SheetButton variant="default" onClick={() => setConfirmDelete(false)}>
                  Cancelar
                </SheetButton>
                <SheetButton color="red" loading={deleteMutation.isPending} onClick={() => deleteMutation.mutate(selected.id)}>
                  Apagar
                </SheetButton>
              </>
            ) : (
              <>
                <SheetButton variant="default" color="red" onClick={() => setConfirmDelete(true)}>
                  Excluir
                </SheetButton>
                <SheetButton color="brand" onClick={() => router.push(`/registro-exercicios/${selected.id}`)}>
                  Abrir treino
                </SheetButton>
              </>
            )
          ) : undefined
        }
      >
        {selected && confirmDelete ? (
          <>
            <Text>Deseja apagar este treino e todos os exercícios lançados nele?</Text>
            <Text size="sm" c="dimmed">
              {selected.name}
            </Text>
            {deleteMutation.isError ? (
              <Text size="sm" c="red" role="alert">
                {deleteMutation.error instanceof Error ? deleteMutation.error.message : 'Não foi possível apagar.'}
              </Text>
            ) : null}
          </>
        ) : selected ? (
          <ListGroup>
            <ListRow title="Data" value={selected.local_date} />
            <ListRow title="Status" value={statusInfo?.label ?? '-'} />
            <ListRow title="Academia" value={selected.gym_name || '-'} />
            <ListRow title="Condutor" value={conductor || '-'} />
            <ListRow title="Carga total" value={`${formatNumberOrDash(selected.total_volume_kg, 1)} kg`} />
            <ListRow title="RPE médio" value={formatNumberOrDash(selected.avg_rpe, 1)} />
            <ListRow title="Ficha vinculada" value={selected.workout_plan_name || '-'} />
          </ListGroup>
        ) : null}
      </BottomSheet>

      <PortalFieldsStyle />
      <NewWorkoutModal
        opened={newOpen}
        onClose={closeNew}
        onSubmit={(form) => startMutation.mutateAsync(form).then(() => undefined)}
        isSubmitting={startMutation.isPending}
        errorMessage={startMutation.isError ? String(startMutation.error) : null}
      />
    </>
  );
}
