'use client';

import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Checkbox,
  Group,
  Loader,
  Modal,
  Paper,
  Select,
  Stack,
  Table,
  Text,
  TextInput,
  Textarea,
  Tooltip,
} from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import {
  IconAlertTriangle,
  IconChevronLeft,
  IconChevronRight,
  IconDeviceFloppy,
  IconPencil,
  IconPlus,
  IconSearch,
  IconTrash,
  IconX,
} from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { apiFetch, ApiError } from '@/lib/api';
import type {
  ExerciseExternalMappingFlatItem,
  ExerciseExternalMappingFlatListResponse,
  ExerciseExternalMappingProvidersResponse,
  ExerciseExternalMappingUpsertResponse,
  ExerciseExternalMappingDeleteResponse,
  ExerciseListResponse,
} from '@/lib/exercise-catalog-admin-types';
import { BRAND } from '@/theme';
import { ConfirmDeleteModal } from './ConfirmDeleteModal';

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

function isForbidden(error: unknown): boolean {
  return error instanceof ApiError && error.status === 403;
}

function isTruthy(value: number | boolean | null | undefined): boolean {
  return value === true || value === 1;
}

// Status reais aceitos pelo backend (`exercise_external_mappings.mapping_status`
// não tem enum no schema, mas o CRUD novo sempre grava um destes três; "PENDING"
// não existe aqui — era usado só pela tela legada `/mapeamentos` do onpremise).
const MAPPING_STATUS_OPTIONS = [
  { value: 'CONFIRMED', label: 'Confirmado' },
  { value: 'PROVISIONAL', label: 'Provisório' },
  { value: 'WEAK_APPROXIMATION', label: 'Aproximação fraca' },
];

function statusColor(status?: string | null): string {
  switch ((status || '').trim().toUpperCase()) {
    case 'CONFIRMED':
      return 'green';
    case 'PROVISIONAL':
      return 'yellow';
    case 'WEAK_APPROXIMATION':
      return 'orange';
    default:
      return 'gray';
  }
}

type RowDraft = {
  external_id: string;
  external_name: string;
  mapping_status: string;
  source: string;
  notes: string;
  is_active: boolean;
};

function draftFrom(item: ExerciseExternalMappingFlatItem): RowDraft {
  return {
    external_id: item.external_id || '',
    external_name: item.external_name || '',
    // dado legado pode ter vindo em minúsculo, ou como "PENDING" (não existe
    // mais como opção válida) — normaliza para maiúsculo e cai em CONFIRMED
    // como default de edição quando o valor salvo não é uma das 3 opções.
    mapping_status: MAPPING_STATUS_OPTIONS.some((option) => option.value === (item.mapping_status || '').toUpperCase())
      ? (item.mapping_status || '').toUpperCase()
      : 'CONFIRMED',
    source: item.source || '',
    notes: item.notes || '',
    is_active: isTruthy(item.is_active),
  };
}

const PAGE_SIZE = 50;

const emptyCreateDraft = {
  exercise_id: null as string | null,
  provider: 'AMAZFIT',
  external_id: '',
  external_name: '',
  mapping_status: 'CONFIRMED',
  source: '',
  notes: '',
};

/**
 * Aba "Mapeamentos externos" de `/admin/dominio-exercicios`: listagem FLAT
 * (`GET /exercise-external-mappings`, uma linha por mapeamento, não agrupada
 * por exercício) com busca, filtro por provider, edição inline e exclusão.
 * Reproduz a UX da extinta tela `/mapeamentos` do onpremise, adaptada ao
 * padrão desta tela (isAdmin via `/auth/me`, `apiFetch` direto, sem proxies).
 */
export function ExternalMappingsPanel({ isAdmin }: { isAdmin: boolean }) {
  const qc = useQueryClient();

  const [search, setSearch] = useState('');
  const [appliedSearch, setAppliedSearch] = useState('');
  const [provider, setProvider] = useState<string | null>(null);
  const [activeOnly, setActiveOnly] = useState(false);
  const [offset, setOffset] = useState(0);

  function runSearch() {
    setAppliedSearch(search.trim());
    setOffset(0);
  }

  const mappingsQuery = useQuery({
    queryKey: ['exercise-external-mappings-flat', appliedSearch, provider, activeOnly, offset],
    queryFn: () => {
      const params = new URLSearchParams({
        query: appliedSearch,
        active_only: String(activeOnly),
        limit: String(PAGE_SIZE),
        offset: String(offset),
      });
      if (provider) params.set('provider', provider);
      return apiFetch<ExerciseExternalMappingFlatListResponse>(`/exercise-external-mappings?${params.toString()}`);
    },
  });

  const providersQuery = useQuery({
    queryKey: ['exercise-external-mapping-providers'],
    queryFn: () => apiFetch<ExerciseExternalMappingProvidersResponse>('/exercise-external-mappings/providers'),
  });

  const items = mappingsQuery.data?.items || [];
  const total = mappingsQuery.data?.total ?? 0;
  const providerOptions = (providersQuery.data?.items || []).map((row) => ({
    value: row.provider,
    label: `${row.provider} (${row.count})`,
  }));

  function invalidateAll() {
    return Promise.all([
      qc.invalidateQueries({ queryKey: ['exercise-external-mappings-flat'] }),
      qc.invalidateQueries({ queryKey: ['exercise-external-mapping-providers'] }),
    ]);
  }

  // ---- edição inline ---------------------------------------------------
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editDraft, setEditDraft] = useState<RowDraft | null>(null);

  function startEdit(item: ExerciseExternalMappingFlatItem) {
    setEditingId(item.id);
    setEditDraft(draftFrom(item));
    saveMutation.reset();
  }

  function cancelEdit() {
    setEditingId(null);
    setEditDraft(null);
  }

  function updateEditDraft<K extends keyof RowDraft>(field: K, value: RowDraft[K]) {
    setEditDraft((current) => (current ? { ...current, [field]: value } : current));
  }

  const saveMutation = useMutation({
    mutationFn: () => {
      if (editingId === null || !editDraft) throw new Error('Nenhuma linha em edição.');
      return apiFetch<ExerciseExternalMappingUpsertResponse>(`/exercises/external-mappings/${editingId}`, {
        method: 'PUT',
        body: JSON.stringify({
          external_id: editDraft.external_id.trim() || null,
          external_name: editDraft.external_name.trim(),
          mapping_status: editDraft.mapping_status,
          source: editDraft.source.trim() || null,
          notes: editDraft.notes.trim() || null,
          is_active: editDraft.is_active,
        }),
      });
    },
    onSuccess: async () => {
      cancelEdit();
      await invalidateAll();
    },
  });

  // ---- exclusão ----------------------------------------------------------
  const [removeOpen, { open: openRemove, close: closeRemove }] = useDisclosure(false);
  const [removeTarget, setRemoveTarget] = useState<ExerciseExternalMappingFlatItem | null>(null);

  const removeMutation = useMutation({
    mutationFn: (id: number) =>
      apiFetch<ExerciseExternalMappingDeleteResponse>(`/exercises/external-mappings/${id}`, { method: 'DELETE' }),
    onSuccess: async () => {
      closeRemove();
      setRemoveTarget(null);
      await invalidateAll();
    },
  });

  // ---- criação (modal) ----------------------------------------------------
  const [createOpen, { open: openCreateRaw, close: closeCreateRaw }] = useDisclosure(false);
  const [createDraft, setCreateDraft] = useState({ ...emptyCreateDraft });
  const [exerciseSearch, setExerciseSearch] = useState('');

  function openCreate() {
    setCreateDraft({ ...emptyCreateDraft });
    setExerciseSearch('');
    createMutation.reset();
    openCreateRaw();
  }

  function closeCreate() {
    closeCreateRaw();
    setCreateDraft({ ...emptyCreateDraft });
    setExerciseSearch('');
  }

  const exercisesQuery = useQuery({
    queryKey: ['exercise-catalog-options-for-mapping', exerciseSearch],
    queryFn: () => {
      const params = new URLSearchParams({ query: exerciseSearch, active_only: 'true', limit: '200' });
      return apiFetch<ExerciseListResponse>(`/exercises?${params.toString()}`);
    },
    enabled: createOpen,
  });

  const exerciseOptions = (exercisesQuery.data?.items || []).map((row) => ({ value: String(row.id), label: row.name }));

  const createMutation = useMutation({
    mutationFn: () => {
      if (!createDraft.exercise_id) throw new Error('Selecione um exercício.');
      if (!createDraft.provider.trim()) throw new Error('Provider é obrigatório.');
      if (!createDraft.external_name.trim()) throw new Error('Nome externo é obrigatório.');

      return apiFetch<ExerciseExternalMappingUpsertResponse>(`/exercises/${createDraft.exercise_id}/external-mappings`, {
        method: 'POST',
        body: JSON.stringify({
          provider: createDraft.provider.trim().toUpperCase(),
          external_id: createDraft.external_id.trim() || null,
          external_name: createDraft.external_name.trim(),
          mapping_status: createDraft.mapping_status,
          source: createDraft.source.trim() || null,
          notes: createDraft.notes.trim() || null,
          is_active: true,
        }),
      });
    },
    onSuccess: async () => {
      closeCreate();
      await invalidateAll();
    },
  });

  const canPrev = offset > 0;
  const canNext = offset + PAGE_SIZE < total;
  const rangeStart = total === 0 ? 0 : offset + 1;
  const rangeEnd = Math.min(offset + PAGE_SIZE, total);

  return (
    <Stack gap="md">
      <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
        <Group justify="space-between" align="flex-end" mb="md" wrap="wrap">
          <Group align="flex-end" wrap="wrap" gap="sm">
            <TextInput
              label="Buscar"
              placeholder="Nome do exercício ou nome externo..."
              value={search}
              onChange={(event) => setSearch(event.currentTarget.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') runSearch();
              }}
              leftSection={<IconSearch size={16} />}
              w={300}
            />
            <Button variant="default" onClick={runSearch}>
              Buscar
            </Button>
            <Select
              label="Provider"
              placeholder="Todos"
              data={providerOptions}
              value={provider}
              onChange={(value) => {
                setProvider(value);
                setOffset(0);
              }}
              clearable
              searchable
              w={200}
            />
            <Checkbox
              label="Só ativos"
              checked={activeOnly}
              onChange={(event) => {
                setActiveOnly(event.currentTarget.checked);
                setOffset(0);
              }}
              mb={8}
            />
            <Button variant="light" color="brand" onClick={() => mappingsQuery.refetch()} loading={mappingsQuery.isFetching}>
              Atualizar
            </Button>
            <Badge color="brand" variant="light" mb={8}>
              {total.toLocaleString('pt-BR')} mapeamento(s)
            </Badge>
          </Group>

          {isAdmin ? (
            <Button color="brand" leftSection={<IconPlus size={16} />} onClick={openCreate}>
              Adicionar
            </Button>
          ) : (
            <Tooltip label="Ação restrita a administradores">
              <Button color="brand" leftSection={<IconPlus size={16} />} disabled>
                Adicionar
              </Button>
            </Tooltip>
          )}
        </Group>

        {mappingsQuery.isError && (
          <Alert color="red" icon={<IconAlertTriangle size={16} />} mb="md">
            {errorMessage(mappingsQuery.error, 'Não foi possível carregar os mapeamentos externos.')}
          </Alert>
        )}

        {saveMutation.isError && (
          <Alert color="red" icon={<IconAlertTriangle size={16} />} mb="md">
            {isForbidden(saveMutation.error)
              ? 'Ação restrita a administradores.'
              : errorMessage(saveMutation.error, 'Não foi possível salvar o mapeamento.')}
          </Alert>
        )}

        {mappingsQuery.isLoading ? (
          <Group>
            <Loader size="sm" color="brand" />
            <Text c={BRAND.textMuted}>Carregando mapeamentos...</Text>
          </Group>
        ) : (
          <>
            <Table.ScrollContainer minWidth={1100}>
              <Table striped highlightOnHover withTableBorder verticalSpacing="xs">
                <Table.Thead>
                  <Table.Tr>
                    <Table.Th>Exercício</Table.Th>
                    <Table.Th w={110}>Provider</Table.Th>
                    <Table.Th w={130}>External ID</Table.Th>
                    <Table.Th>Nome externo</Table.Th>
                    <Table.Th w={170}>Status</Table.Th>
                    <Table.Th w={130}>Fonte</Table.Th>
                    <Table.Th>Notas</Table.Th>
                    <Table.Th w={80}>Ativo</Table.Th>
                    <Table.Th w={100} />
                  </Table.Tr>
                </Table.Thead>
                <Table.Tbody>
                  {items.map((item) => {
                    const editing = editingId === item.id && editDraft;

                    if (editing && editDraft) {
                      return (
                        <Table.Tr key={item.id}>
                          <Table.Td c={BRAND.textPrimary}>{item.exercise_name}</Table.Td>
                          <Table.Td>
                            <Badge variant="light" color="brand">
                              {item.provider}
                            </Badge>
                          </Table.Td>
                          <Table.Td>
                            <TextInput
                              size="xs"
                              value={editDraft.external_id}
                              onChange={(event) => updateEditDraft('external_id', event.currentTarget.value)}
                            />
                          </Table.Td>
                          <Table.Td>
                            <TextInput
                              size="xs"
                              value={editDraft.external_name}
                              onChange={(event) => updateEditDraft('external_name', event.currentTarget.value)}
                            />
                          </Table.Td>
                          <Table.Td>
                            <Select
                              size="xs"
                              data={MAPPING_STATUS_OPTIONS}
                              value={editDraft.mapping_status}
                              onChange={(value) => updateEditDraft('mapping_status', value || 'CONFIRMED')}
                              allowDeselect={false}
                            />
                          </Table.Td>
                          <Table.Td>
                            <TextInput
                              size="xs"
                              value={editDraft.source}
                              onChange={(event) => updateEditDraft('source', event.currentTarget.value)}
                            />
                          </Table.Td>
                          <Table.Td>
                            <TextInput
                              size="xs"
                              value={editDraft.notes}
                              onChange={(event) => updateEditDraft('notes', event.currentTarget.value)}
                            />
                          </Table.Td>
                          <Table.Td>
                            <Checkbox
                              checked={editDraft.is_active}
                              onChange={(event) => updateEditDraft('is_active', event.currentTarget.checked)}
                            />
                          </Table.Td>
                          <Table.Td>
                            <Group gap={4} wrap="nowrap">
                              <Tooltip label="Salvar">
                                <ActionIcon
                                  variant="light"
                                  color="green"
                                  loading={saveMutation.isPending}
                                  disabled={!editDraft.external_name.trim()}
                                  onClick={() => saveMutation.mutate()}
                                >
                                  <IconDeviceFloppy size={16} />
                                </ActionIcon>
                              </Tooltip>
                              <Tooltip label="Cancelar">
                                <ActionIcon variant="subtle" color="gray" onClick={cancelEdit}>
                                  <IconX size={16} />
                                </ActionIcon>
                              </Tooltip>
                            </Group>
                          </Table.Td>
                        </Table.Tr>
                      );
                    }

                    return (
                      <Table.Tr key={item.id} opacity={isTruthy(item.is_active) ? 1 : 0.55}>
                        <Table.Td c={BRAND.textPrimary}>{item.exercise_name}</Table.Td>
                        <Table.Td>
                          <Badge variant="light" color="brand">
                            {item.provider}
                          </Badge>
                        </Table.Td>
                        <Table.Td c={BRAND.textMuted}>{item.external_id || '-'}</Table.Td>
                        <Table.Td c={BRAND.textPrimary}>{item.external_name}</Table.Td>
                        <Table.Td>
                          <Badge color={statusColor(item.mapping_status)} variant="light">
                            {item.mapping_status || '-'}
                          </Badge>
                        </Table.Td>
                        <Table.Td c={BRAND.textMuted}>{item.source || '-'}</Table.Td>
                        <Table.Td c={BRAND.textMuted}>{item.notes || '-'}</Table.Td>
                        <Table.Td>
                          <Badge color={isTruthy(item.is_active) ? 'green' : 'gray'} variant="light">
                            {isTruthy(item.is_active) ? 'sim' : 'não'}
                          </Badge>
                        </Table.Td>
                        <Table.Td>
                          {isAdmin ? (
                            <Group gap={4} wrap="nowrap">
                              <Tooltip label="Editar">
                                <ActionIcon variant="light" color="brand" onClick={() => startEdit(item)}>
                                  <IconPencil size={16} />
                                </ActionIcon>
                              </Tooltip>
                              <Tooltip label="Excluir">
                                <ActionIcon
                                  variant="light"
                                  color="red"
                                  onClick={() => {
                                    setRemoveTarget(item);
                                    removeMutation.reset();
                                    openRemove();
                                  }}
                                >
                                  <IconTrash size={16} />
                                </ActionIcon>
                              </Tooltip>
                            </Group>
                          ) : (
                            <Tooltip label="Ação restrita a administradores">
                              <Group gap={4} wrap="nowrap">
                                <ActionIcon variant="light" color="brand" disabled>
                                  <IconPencil size={16} />
                                </ActionIcon>
                                <ActionIcon variant="light" color="red" disabled>
                                  <IconTrash size={16} />
                                </ActionIcon>
                              </Group>
                            </Tooltip>
                          )}
                        </Table.Td>
                      </Table.Tr>
                    );
                  })}

                  {items.length === 0 && (
                    <Table.Tr>
                      <Table.Td colSpan={9}>
                        <Text c={BRAND.textMuted} ta="center">
                          Nenhum mapeamento externo encontrado com esses filtros.
                        </Text>
                      </Table.Td>
                    </Table.Tr>
                  )}
                </Table.Tbody>
              </Table>
            </Table.ScrollContainer>

            <Group justify="space-between" mt="md">
              <Text size="sm" c={BRAND.textMuted}>
                {total === 0 ? 'Nenhum resultado' : `${rangeStart}–${rangeEnd} de ${total.toLocaleString('pt-BR')}`}
              </Text>
              <Group gap={4}>
                <ActionIcon variant="light" color="brand" disabled={!canPrev} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
                  <IconChevronLeft size={16} />
                </ActionIcon>
                <ActionIcon variant="light" color="brand" disabled={!canNext} onClick={() => setOffset(offset + PAGE_SIZE)}>
                  <IconChevronRight size={16} />
                </ActionIcon>
              </Group>
            </Group>
          </>
        )}
      </Paper>

      {/* ---- Modal: novo mapeamento externo (POST /exercises/{id}/external-mappings) --- */}
      <Modal opened={createOpen} onClose={closeCreate} title="Adicionar mapeamento externo" size="lg" centered>
        <Stack gap="md">
          <Select
            label="Exercício"
            placeholder="Busque pelo nome do exercício canônico"
            data={exerciseOptions}
            value={createDraft.exercise_id}
            onChange={(value) => setCreateDraft((current) => ({ ...current, exercise_id: value }))}
            searchable
            searchValue={exerciseSearch}
            onSearchChange={setExerciseSearch}
            nothingFoundMessage={exercisesQuery.isFetching ? 'Buscando...' : 'Nenhum exercício encontrado'}
            required
          />

          <Group grow align="start">
            <TextInput
              label="Provider"
              value={createDraft.provider}
              onChange={(event) => setCreateDraft((current) => ({ ...current, provider: event.currentTarget.value }))}
              required
            />
            <TextInput
              label="External ID"
              value={createDraft.external_id}
              onChange={(event) => setCreateDraft((current) => ({ ...current, external_id: event.currentTarget.value }))}
            />
          </Group>

          <TextInput
            label="Nome externo"
            placeholder="Como o exercício aparece no provider"
            value={createDraft.external_name}
            onChange={(event) => setCreateDraft((current) => ({ ...current, external_name: event.currentTarget.value }))}
            required
          />

          <Group grow align="start">
            <Select
              label="Status"
              data={MAPPING_STATUS_OPTIONS}
              value={createDraft.mapping_status}
              onChange={(value) => setCreateDraft((current) => ({ ...current, mapping_status: value || 'CONFIRMED' }))}
              allowDeselect={false}
            />
            <TextInput
              label="Fonte"
              value={createDraft.source}
              onChange={(event) => setCreateDraft((current) => ({ ...current, source: event.currentTarget.value }))}
            />
          </Group>

          <Textarea
            label="Notas"
            minRows={2}
            value={createDraft.notes}
            onChange={(event) => setCreateDraft((current) => ({ ...current, notes: event.currentTarget.value }))}
          />

          {createMutation.isError && (
            <Alert color="red" icon={<IconAlertTriangle size={16} />}>
              {isForbidden(createMutation.error)
                ? 'Ação restrita a administradores.'
                : errorMessage(createMutation.error, 'Não foi possível criar o mapeamento externo.')}
            </Alert>
          )}

          <Group justify="flex-end">
            <Button variant="default" onClick={closeCreate}>
              Cancelar
            </Button>
            <Button
              color="brand"
              leftSection={<IconDeviceFloppy size={16} />}
              loading={createMutation.isPending}
              disabled={!createDraft.exercise_id || !createDraft.provider.trim() || !createDraft.external_name.trim()}
              onClick={() => createMutation.mutate()}
            >
              Salvar
            </Button>
          </Group>
        </Stack>
      </Modal>

      <ConfirmDeleteModal
        opened={removeOpen}
        title="Excluir mapeamento externo"
        message={`Excluir o mapeamento "${removeTarget?.provider}: ${removeTarget?.external_name}" do exercício "${removeTarget?.exercise_name}"? Esta remoção é definitiva.`}
        onClose={() => {
          closeRemove();
          setRemoveTarget(null);
        }}
        onConfirm={() => removeTarget && removeMutation.mutate(removeTarget.id)}
        loading={removeMutation.isPending}
        error={removeMutation.error}
        fallbackErrorMessage="Não foi possível excluir o mapeamento externo."
      />
    </Stack>
  );
}
