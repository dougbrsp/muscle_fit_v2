'use client';

import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Group,
  Loader,
  Modal,
  Pagination,
  Paper,
  Select,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
  Tooltip,
} from '@mantine/core';
import { IconAlertTriangle, IconArrowRight, IconPlus, IconTrash } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useDisclosure, useMediaQuery } from '@mantine/hooks';
import { useRouter } from 'next/navigation';
import { useEffect, useMemo, useState } from 'react';
import { apiFetch } from '@/lib/api';
import { NewWorkoutModal } from '@/components/NewWorkoutModal';
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
import { BRAND } from '@/theme';
import { formatNumberOrDash } from '@/lib/format';

const PAGE_SIZE = 20;
// Busca um lote grande já filtrado pelo backend (q/período/academia/condutor)
// e pagina 20/página no cliente — GET /activities não devolve contagem total.
const FETCH_LIMIT = 200;
// Largura máxima dos nomes (treino e ficha) na tabela: o resto vira reticências.
const NAME_MAX_WIDTH = 200;

const periodOptions = [
  { value: 'all', label: 'Todo período' },
  { value: '7', label: 'Últimos 7 dias' },
  { value: '14', label: 'Últimos 14 dias' },
  { value: '30', label: 'Últimos 30 dias' },
  { value: '90', label: 'Últimos 90 dias' },
];

type StartSessionResult = { session_id: number };

export default function RegistroExerciciosPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [searchText, setSearchText] = useState('');
  const [periodDays, setPeriodDays] = useState('all');
  const [gymFilter, setGymFilter] = useState('');
  const [conductedByFilter, setConductedByFilter] = useState('');
  const [currentPage, setCurrentPage] = useState(1);
  const [modalOpened, { open: openModal, close: closeModal }] = useDisclosure(false);
  const [deleteTarget, setDeleteTarget] = useState<WorkoutListItem | null>(null);
  const [deleteOpen, { open: openDelete, close: closeDelete }] = useDisclosure(false);
  const isMobile = useMediaQuery('(max-width: 48em)');

  useEffect(() => {
    setCurrentPage(1);
  }, [searchText, periodDays, gymFilter, conductedByFilter]);

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
      closeModal();
      await queryClient.invalidateQueries({ queryKey: ['activities'] });
      router.push(`/registro-exercicios/${result.session_id}`);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: async (activityId: number) => apiFetch(`/activities/${activityId}`, { method: 'DELETE' }),
    onSuccess: async () => {
      closeDelete();
      setDeleteTarget(null);
      await queryClient.invalidateQueries({ queryKey: ['activities'] });
    },
  });

  function openDeleteConfirm(workout: WorkoutListItem) {
    setDeleteTarget(workout);
    openDelete();
  }

  const items = listQuery.data?.items || [];
  const totalPages = Math.max(1, Math.ceil(items.length / PAGE_SIZE));
  const paginatedItems = useMemo(() => {
    const start = (currentPage - 1) * PAGE_SIZE;
    return items.slice(start, start + PAGE_SIZE);
  }, [items, currentPage]);

  return (
    <Stack gap="xl">
      <Group justify="space-between" align="flex-start">
        <div>
          <Title order={1} c={BRAND.textPrimary}>
            Registro de exercícios
          </Title>
          <Text c={BRAND.textMuted} size="md">
            Treinos lançados, carga total e RPE médio por sessão.
          </Text>
        </div>
        <Button leftSection={<IconPlus size={16} />} onClick={openModal}>
          Novo treino
        </Button>
      </Group>

      <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
        <Group justify="space-between" mb="md">
          <Title order={3} c={BRAND.textPrimary}>
            Treinos lançados
          </Title>
          <Badge color="brand" variant="light">
            {items.length} itens
          </Badge>
        </Group>

        <Group grow mb="md">
          <TextInput
            label="Buscar treino"
            placeholder="Digite o nome do treino"
            value={searchText}
            onChange={(event) => setSearchText(event.currentTarget.value)}
          />
          <Select label="Período" value={periodDays} onChange={(value) => setPeriodDays(value || 'all')} data={periodOptions} />
          <Select
            searchable
            label="Academia"
            placeholder="Todas"
            value={gymFilter || null}
            onChange={(value) => setGymFilter(value || '')}
            data={gymOptions}
            clearable
          />
          <Select
            label="Condutor"
            placeholder="Todos"
            value={conductedByFilter || null}
            onChange={(value) => setConductedByFilter(value || '')}
            data={conductedByOptions}
            clearable
          />
        </Group>

        {listQuery.isLoading && (
          <Group>
            <Loader size="sm" color="brand" />
            <Text c={BRAND.textMuted}>Carregando treinos...</Text>
          </Group>
        )}

        {listQuery.isError && (
          <Alert color="red" icon={<IconAlertTriangle size={16} />} title="Erro ao carregar treinos">
            {listQuery.error instanceof Error ? listQuery.error.message : 'Não foi possível consultar a Fitness API.'}
          </Alert>
        )}

        {!listQuery.isLoading && !listQuery.isError && (
          // Fonte `xs` e sem quebra de linha: cada treino cabe numa linha só. Nome
          // do treino e da ficha cortam com reticências (texto inteiro no `title`);
          // em tela estreita o `ScrollContainer` rola na horizontal.
          <Table.ScrollContainer minWidth={800}>
            <Table striped highlightOnHover withTableBorder fz="xs" style={{ whiteSpace: 'nowrap' }}>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Data</Table.Th>
                  <Table.Th>Treino</Table.Th>
                  <Table.Th>Academia</Table.Th>
                  <Table.Th>Condutor</Table.Th>
                  <Table.Th>Carga total</Table.Th>
                  <Table.Th>RPE médio</Table.Th>
                  <Table.Th>Ficha vinculada</Table.Th>
                  <Table.Th>Status</Table.Th>
                  <Table.Th>Ações</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {paginatedItems.map((workout) => {
                  const statusInfo = workout.session_status ? sessionStatusLabels[workout.session_status] : null;
                  return (
                    <Table.Tr key={workout.id}>
                      <Table.Td>{workout.local_date}</Table.Td>
                      <Table.Td>
                        <Text inherit truncate="end" maw={NAME_MAX_WIDTH} title={workout.name}>
                          {workout.name}
                        </Text>
                      </Table.Td>
                      <Table.Td>{workout.gym_name || '-'}</Table.Td>
                      <Table.Td>
                        {conductedByOptions.find((o) => o.value === workout.conducted_by)?.label ||
                          workout.conducted_by ||
                          '-'}
                      </Table.Td>
                      <Table.Td>{formatNumberOrDash(workout.total_volume_kg, 1)} kg</Table.Td>
                      <Table.Td>{formatNumberOrDash(workout.avg_rpe, 1)}</Table.Td>
                      <Table.Td>
                        <Text inherit truncate="end" maw={NAME_MAX_WIDTH} title={workout.workout_plan_name || undefined}>
                          {workout.workout_plan_name || '-'}
                        </Text>
                      </Table.Td>
                      <Table.Td>
                        {statusInfo ? (
                          <Badge color={statusInfo.color} variant="light">
                            {statusInfo.label}
                          </Badge>
                        ) : (
                          '-'
                        )}
                      </Table.Td>
                      <Table.Td>
                        <Group gap="xs" wrap="nowrap">
                          <Tooltip label="Abrir treino">
                            <ActionIcon variant="light" color="brand" onClick={() => router.push(`/registro-exercicios/${workout.id}`)}>
                              <IconArrowRight size={16} />
                            </ActionIcon>
                          </Tooltip>
                          <Tooltip label="Excluir treino">
                            <ActionIcon
                              variant="subtle"
                              color="red"
                              onClick={() => openDeleteConfirm(workout)}
                              loading={deleteMutation.isPending && deleteTarget?.id === workout.id}
                            >
                              <IconTrash size={16} />
                            </ActionIcon>
                          </Tooltip>
                        </Group>
                      </Table.Td>
                    </Table.Tr>
                  );
                })}
                {paginatedItems.length === 0 && (
                  <Table.Tr>
                    <Table.Td colSpan={9}>
                      <Text c={BRAND.textMuted} ta="center" py="md">
                        Nenhum treino encontrado com os filtros atuais.
                      </Text>
                    </Table.Td>
                  </Table.Tr>
                )}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        )}

        {items.length > PAGE_SIZE && (
          <Group justify="center" mt="md">
            <Pagination total={totalPages} value={currentPage} onChange={setCurrentPage} color="brand" />
          </Group>
        )}
      </Paper>

      <Modal
        opened={deleteOpen}
        onClose={closeDelete}
        title="Confirmar exclusão"
        size="sm"
        fullScreen={isMobile}
        radius={isMobile ? 0 : undefined}
      >
        <Stack gap="sm">
          <Text>Deseja apagar este treino e todos os exercícios lançados nele?</Text>
          <Text size="sm" c="dimmed">
            {deleteTarget ? deleteTarget.name : ''}
          </Text>
          <Group justify="flex-end">
            <Button variant="default" onClick={closeDelete}>
              Cancelar
            </Button>
            <Button
              color="red"
              loading={deleteMutation.isPending}
              onClick={() => deleteTarget && deleteMutation.mutate(deleteTarget.id)}
            >
              Apagar
            </Button>
          </Group>
        </Stack>
      </Modal>

      <NewWorkoutModal
        opened={modalOpened}
        onClose={closeModal}
        onSubmit={(form) => startMutation.mutateAsync(form).then(() => undefined)}
        isSubmitting={startMutation.isPending}
        errorMessage={startMutation.isError ? String(startMutation.error) : null}
      />
    </Stack>
  );
}
