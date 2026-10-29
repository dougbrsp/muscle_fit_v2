'use client';

import {
  ActionIcon,
  Alert,
  Button,
  Group,
  Loader,
  Modal,
  NumberInput,
  Paper,
  Stack,
  Table,
  Text,
  TextInput,
  Title,
  Tooltip,
} from '@mantine/core';
import { useDisclosure, useMediaQuery } from '@mantine/hooks';
import { IconAlertTriangle, IconEdit, IconPlus, IconTrash } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { ChangeEvent } from 'react';
import { useState } from 'react';
import { TablePager } from '@/components/TablePager';
import { apiFetch, ApiError } from '@/lib/api';
import { usePagination } from '@/lib/pagination';
import {
  ApiListResponse,
  decimalHoursToHHMM,
  emptySleepForm,
  formatDateBR,
  formatNumberOrDash,
  SleepFormState,
  sleepEntryToFormState,
  sleepFormToApiPayload,
  SleepMetricsEntry,
} from '@/lib/physical-indicators-types';
import { BRAND } from '@/theme';

function SleepForm({
  initial,
  onSubmit,
  loading,
}: {
  initial: SleepFormState;
  onSubmit: (data: SleepFormState) => void;
  loading: boolean;
}) {
  const [form, setForm] = useState<SleepFormState>(initial);

  function num(field: 'regularity_percent' | 'awake_count') {
    const value = form[field];
    return {
      value: value ?? undefined,
      onChange: (val: number | string) =>
        setForm((prev) => ({ ...prev, [field]: val === '' || val === undefined ? null : Number(val) })),
    };
  }

  function time(field: 'duration_hours' | 'deep_sleep_hours' | 'rem_sleep_hours' | 'awake_hours') {
    return {
      value: form[field] ?? '',
      onChange: (event: ChangeEvent<HTMLInputElement>) => {
        const val = event.currentTarget.value;
        setForm((prev) => ({ ...prev, [field]: val }));
      },
    };
  }

  return (
    <Stack gap="sm">
      <TextInput
        label="Data"
        required
        type="date"
        value={form.local_date ?? ''}
        onChange={(event) => {
          const dateVal = event.currentTarget.value;
          setForm((prev) => ({ ...prev, local_date: dateVal }));
        }}
      />

      <Group grow>
        <TextInput label="Duração (hh:mm)" placeholder="07:30" {...time('duration_hours')} />
        <NumberInput label="Regularidade (%)" decimalScale={2} step={1} {...num('regularity_percent')} />
      </Group>

      <Group grow>
        <TextInput label="Sono profundo (hh:mm)" placeholder="01:30" {...time('deep_sleep_hours')} />
        <TextInput label="Sono REM (hh:mm)" placeholder="01:15" {...time('rem_sleep_hours')} />
      </Group>

      <Group grow>
        <NumberInput label="Despertares" decimalScale={0} step={1} {...num('awake_count')} />
        <TextInput label="Tempo acordado (hh:mm)" placeholder="00:10" {...time('awake_hours')} />
      </Group>

      <TextInput
        label="Observações"
        value={form.notes ?? ''}
        onChange={(event) => {
          const noteVal = event.currentTarget.value;
          setForm((prev) => ({ ...prev, notes: noteVal || null }));
        }}
      />

      <Button mt="sm" color="brand" loading={loading} onClick={() => onSubmit(form)}>
        Salvar
      </Button>
    </Stack>
  );
}

export function SleepMetricsSection() {
  const qc = useQueryClient();
  const isMobile = useMediaQuery('(max-width: 48em)');
  const [modalOpened, { open: openModal, close: closeModal }] = useDisclosure(false);
  const [editing, setEditing] = useState<SleepMetricsEntry | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<SleepMetricsEntry | null>(null);
  const [deleteOpened, { open: openDelete, close: closeDelete }] = useDisclosure(false);

  const listQuery = useQuery({
    queryKey: ['sleep-metrics'],
    queryFn: () => apiFetch<ApiListResponse<SleepMetricsEntry>>('/sleep-metrics'),
  });

  // POST /sleep-metrics é create-or-update por local_date (UNIQUE composta
  // user_id+local_date no backend) — mesmo em "novo lançamento" usamos POST,
  // e PUT só quando já sabemos o id (edição a partir da tabela).
  const saveMutation = useMutation({
    mutationFn: (data: { id?: number; form: SleepFormState }) =>
      data.id
        ? apiFetch(`/sleep-metrics/${data.id}`, { method: 'PUT', body: JSON.stringify(sleepFormToApiPayload(data.form)) })
        : apiFetch('/sleep-metrics', { method: 'POST', body: JSON.stringify(sleepFormToApiPayload(data.form)) }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['sleep-metrics'] });
      closeModal();
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => apiFetch(`/sleep-metrics/${id}`, { method: 'DELETE' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['sleep-metrics'] });
      closeDelete();
      setDeleteTarget(null);
    },
  });

  function handleOpenCreate() {
    setEditing(null);
    openModal();
  }

  function handleOpenEdit(entry: SleepMetricsEntry) {
    setEditing(entry);
    openModal();
  }

  function handleOpenDelete(entry: SleepMetricsEntry) {
    setDeleteTarget(entry);
    openDelete();
  }

  function handleSave(form: SleepFormState) {
    saveMutation.mutate({ id: editing?.id, form });
  }

  const items = listQuery.data?.items || [];
  const pagination = usePagination(items);

  return (
    <Stack gap="md">
      <Group justify="space-between" align="center">
        <div>
          <Title order={2} c={BRAND.textPrimary}>
            Sono
          </Title>
          <Text c={BRAND.textMuted} size="sm">
            Lançamento manual — duração, regularidade, sono profundo/REM e despertares.
          </Text>
        </div>
        <Button leftSection={<IconPlus size={16} />} color="brand" onClick={handleOpenCreate}>
          Novo lançamento
        </Button>
      </Group>

      {listQuery.isLoading && (
        <Group>
          <Loader size="sm" color="brand" />
          <Text c={BRAND.textMuted}>Carregando dados de sono...</Text>
        </Group>
      )}

      {listQuery.isError && (
        <Alert color="red" icon={<IconAlertTriangle />} title="Erro ao carregar dados">
          {listQuery.error instanceof ApiError ? listQuery.error.message : 'Não foi possível consultar a Fitness API.'}
        </Alert>
      )}

      {!listQuery.isLoading && !listQuery.isError && (
        <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
          <Table.ScrollContainer minWidth={900}>
            <Table striped highlightOnHover withTableBorder verticalSpacing="sm">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Data</Table.Th>
                  <Table.Th>Duração (hh:mm)</Table.Th>
                  <Table.Th>Regularidade (%)</Table.Th>
                  <Table.Th>Sono profundo (hh:mm)</Table.Th>
                  <Table.Th>Sono REM (hh:mm)</Table.Th>
                  <Table.Th>Despertares</Table.Th>
                  <Table.Th>Tempo acordado (hh:mm)</Table.Th>
                  <Table.Th>Observações</Table.Th>
                  <Table.Th w={80}>Ações</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {pagination.pageItems.map((entry) => (
                  <Table.Tr key={entry.id}>
                    <Table.Td style={{ whiteSpace: 'nowrap' }}>{formatDateBR(entry.local_date)}</Table.Td>
                    <Table.Td>{decimalHoursToHHMM(entry.duration_hours) || '-'}</Table.Td>
                    <Table.Td>{formatNumberOrDash(entry.regularity_percent)}</Table.Td>
                    <Table.Td>{decimalHoursToHHMM(entry.deep_sleep_hours) || '-'}</Table.Td>
                    <Table.Td>{decimalHoursToHHMM(entry.rem_sleep_hours) || '-'}</Table.Td>
                    <Table.Td>{entry.awake_count ?? '-'}</Table.Td>
                    <Table.Td>{decimalHoursToHHMM(entry.awake_hours) || '-'}</Table.Td>
                    <Table.Td>{entry.notes ?? '-'}</Table.Td>
                    <Table.Td>
                      <Group gap="xs" wrap="nowrap">
                        <Tooltip label="Editar">
                          <ActionIcon variant="subtle" color="brand" onClick={() => handleOpenEdit(entry)}>
                            <IconEdit size={16} />
                          </ActionIcon>
                        </Tooltip>
                        <Tooltip label="Excluir">
                          <ActionIcon variant="subtle" color="red" onClick={() => handleOpenDelete(entry)}>
                            <IconTrash size={16} />
                          </ActionIcon>
                        </Tooltip>
                      </Group>
                    </Table.Td>
                  </Table.Tr>
                ))}
                {items.length === 0 && (
                  <Table.Tr>
                    <Table.Td colSpan={9}>
                      <Text c={BRAND.textMuted} ta="center" py="md">
                        Nenhum lançamento encontrado. Clique em &quot;Novo lançamento&quot; para começar.
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

      <Modal
        opened={modalOpened}
        onClose={closeModal}
        title={editing ? 'Editar lançamento' : 'Novo lançamento'}
        size="lg"
        fullScreen={isMobile}
        radius={isMobile ? 0 : undefined}
      >
        <SleepForm
          key={editing?.id ?? 'new'}
          initial={editing ? sleepEntryToFormState(editing) : emptySleepForm()}
          onSubmit={handleSave}
          loading={saveMutation.isPending}
        />
        {saveMutation.isError && (
          <Alert mt="sm" color="red" icon={<IconAlertTriangle />}>
            {saveMutation.error instanceof ApiError ? saveMutation.error.message : 'Não foi possível salvar.'}
          </Alert>
        )}
      </Modal>

      <Modal
        opened={deleteOpened}
        onClose={closeDelete}
        title="Confirmar exclusão"
        size="sm"
        fullScreen={isMobile}
        radius={isMobile ? 0 : undefined}
      >
        <Stack gap="md">
          <Text c={BRAND.textPrimary}>
            Excluir o lançamento de sono de{' '}
            <Text component="span" fw={700}>
              {deleteTarget ? formatDateBR(deleteTarget.local_date) : ''}
            </Text>
            ? Esta ação não pode ser desfeita.
          </Text>

          {deleteMutation.isError && (
            <Alert color="red" icon={<IconAlertTriangle />} title="Erro ao excluir">
              {deleteMutation.error instanceof ApiError ? deleteMutation.error.message : 'Não foi possível excluir.'}
            </Alert>
          )}

          <Group justify="flex-end">
            <Button variant="default" onClick={closeDelete} disabled={deleteMutation.isPending}>
              Cancelar
            </Button>
            <Button
              color="red"
              loading={deleteMutation.isPending}
              onClick={() => deleteTarget && deleteMutation.mutate(deleteTarget.id)}
            >
              Excluir
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
