'use client';

import { ActionIcon, Alert, Button, Group, Loader, Modal, Paper, Stack, Table, Text, Textarea, TextInput, Tooltip } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { IconAlertTriangle, IconPencil, IconPlus, IconTrash } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { apiFetch, ApiError } from '@/lib/api';
import type {
  ApiListResponse,
  EquipmentType,
  EquipmentTypeDeleteResponse,
  EquipmentTypeUpsertPayload,
  EquipmentTypeUpsertResponse,
} from '@/lib/exercise-catalog-admin-types';
import { BRAND } from '@/theme';
import { ConfirmDeleteModal } from './ConfirmDeleteModal';

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

function isForbidden(error: unknown): boolean {
  return error instanceof ApiError && error.status === 403;
}

/**
 * CRUD simples de `equipment_types` (poucas linhas — catálogo fixo de tipos
 * de equipamento, ex.: "Máquina", "Halteres", "Barra livre").
 *
 * Delete é bloqueado pelo backend (409) enquanto houver `equipment` vinculado
 * ao tipo — a UI exibe a mensagem de erro do backend em vez de tentar
 * calcular o uso no cliente.
 */
export function EquipmentTypesPanel({ isAdmin }: { isAdmin: boolean }) {
  const qc = useQueryClient();

  const typesQuery = useQuery({
    queryKey: ['exercise-catalog-admin-equipment-types'],
    queryFn: () => apiFetch<ApiListResponse<EquipmentType>>('/equipment-types'),
  });
  const types = typesQuery.data?.items || [];

  const [formOpen, { open: openForm, close: closeFormRaw }] = useDisclosure(false);
  const [editing, setEditing] = useState<EquipmentType | null>(null);
  const [code, setCode] = useState('');
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');

  function closeForm() {
    closeFormRaw();
    saveMutation.reset();
  }

  function startCreate() {
    setEditing(null);
    setCode('');
    setName('');
    setDescription('');
    openForm();
  }

  function startEdit(type: EquipmentType) {
    setEditing(type);
    setCode(type.code);
    setName(type.name);
    setDescription(type.description || '');
    openForm();
  }

  const saveMutation = useMutation({
    mutationFn: (payload: EquipmentTypeUpsertPayload) =>
      editing
        ? apiFetch<EquipmentTypeUpsertResponse>(`/equipment-types/${editing.id}`, {
            method: 'PUT',
            body: JSON.stringify(payload),
          })
        : apiFetch<EquipmentTypeUpsertResponse>('/equipment-types', {
            method: 'POST',
            body: JSON.stringify(payload),
          }),
    onSuccess: async () => {
      closeForm();
      await qc.invalidateQueries({ queryKey: ['exercise-catalog-admin-equipment-types'] });
    },
  });

  function submitForm() {
    saveMutation.mutate({ code: code.trim().toUpperCase(), name: name.trim(), description: description.trim() || null });
  }

  const [removeOpen, { open: openRemove, close: closeRemove }] = useDisclosure(false);
  const [removeTarget, setRemoveTarget] = useState<EquipmentType | null>(null);

  const removeMutation = useMutation({
    mutationFn: (id: number) => apiFetch<EquipmentTypeDeleteResponse>(`/equipment-types/${id}`, { method: 'DELETE' }),
    onSuccess: async () => {
      closeRemove();
      setRemoveTarget(null);
      await qc.invalidateQueries({ queryKey: ['exercise-catalog-admin-equipment-types'] });
    },
  });

  return (
    <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
      <Group justify="space-between" align="flex-end" mb="md" wrap="wrap">
        <div>
          <Text fw={600} c={BRAND.textPrimary}>
            Tipos de equipamento
          </Text>
          <Text size="sm" c={BRAND.textMuted}>
            Catálogo fixo (<code>equipment_types</code>) usado para classificar cada equipamento cadastrado em
            Academias.
          </Text>
        </div>
        {isAdmin ? (
          <Button color="brand" leftSection={<IconPlus size={16} />} onClick={startCreate}>
            Novo tipo
          </Button>
        ) : (
          <Tooltip label="Ação restrita a administradores">
            <Button color="brand" leftSection={<IconPlus size={16} />} disabled>
              Novo tipo
            </Button>
          </Tooltip>
        )}
      </Group>

      {typesQuery.isError && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />} mb="md">
          {errorMessage(typesQuery.error, 'Não foi possível carregar os tipos de equipamento.')}
        </Alert>
      )}
      {removeMutation.isError && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />} mb="md">
          {isForbidden(removeMutation.error)
            ? 'Ação restrita a administradores.'
            : errorMessage(removeMutation.error, 'Não foi possível excluir o tipo de equipamento.')}
        </Alert>
      )}

      {typesQuery.isLoading && (
        <Group>
          <Loader size="sm" color="brand" />
          <Text c={BRAND.textMuted}>Carregando tipos de equipamento...</Text>
        </Group>
      )}

      {!typesQuery.isLoading && !typesQuery.isError && (
        <Table.ScrollContainer minWidth={500}>
          <Table striped highlightOnHover withTableBorder verticalSpacing="sm">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Código</Table.Th>
                <Table.Th>Nome</Table.Th>
                <Table.Th>Descrição</Table.Th>
                <Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {types.map((type) => (
                <Table.Tr key={type.id}>
                  <Table.Td c={BRAND.textPrimary}>{type.code}</Table.Td>
                  <Table.Td c={BRAND.textPrimary}>{type.name}</Table.Td>
                  <Table.Td c={BRAND.textMuted}>{type.description || '-'}</Table.Td>
                  <Table.Td>
                    <Group gap={4} wrap="nowrap">
                      {isAdmin ? (
                        <Tooltip label="Editar tipo">
                          <ActionIcon variant="subtle" onClick={() => startEdit(type)}>
                            <IconPencil size={16} />
                          </ActionIcon>
                        </Tooltip>
                      ) : (
                        <Tooltip label="Ação restrita a administradores">
                          <ActionIcon variant="subtle" disabled>
                            <IconPencil size={16} />
                          </ActionIcon>
                        </Tooltip>
                      )}
                      {isAdmin ? (
                        <Tooltip label="Excluir tipo">
                          <ActionIcon
                            color="red"
                            variant="subtle"
                            onClick={() => {
                              setRemoveTarget(type);
                              removeMutation.reset();
                              openRemove();
                            }}
                          >
                            <IconTrash size={16} />
                          </ActionIcon>
                        </Tooltip>
                      ) : (
                        <Tooltip label="Ação restrita a administradores">
                          <ActionIcon color="red" variant="subtle" disabled>
                            <IconTrash size={16} />
                          </ActionIcon>
                        </Tooltip>
                      )}
                    </Group>
                  </Table.Td>
                </Table.Tr>
              ))}
              {types.length === 0 && (
                <Table.Tr>
                  <Table.Td colSpan={4}>
                    <Text c={BRAND.textMuted} ta="center">
                      Nenhum tipo de equipamento cadastrado.
                    </Text>
                  </Table.Td>
                </Table.Tr>
              )}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      )}

      <Modal opened={formOpen} onClose={closeForm} title={editing ? 'Editar tipo de equipamento' : 'Novo tipo de equipamento'} centered>
        <Stack gap="sm">
          <TextInput
            label="Código"
            placeholder="Ex.: MACHINE"
            value={code}
            onChange={(event) => setCode(event.currentTarget.value)}
            required
          />
          <TextInput
            label="Nome"
            placeholder="Ex.: Máquina"
            value={name}
            onChange={(event) => setName(event.currentTarget.value)}
            required
          />
          <Textarea
            label="Descrição"
            value={description}
            onChange={(event) => setDescription(event.currentTarget.value)}
            autosize
            minRows={2}
          />
          {saveMutation.isError && (
            <Alert color="red" icon={<IconAlertTriangle size={16} />}>
              {isForbidden(saveMutation.error)
                ? 'Ação restrita a administradores.'
                : errorMessage(saveMutation.error, 'Não foi possível salvar o tipo de equipamento.')}
            </Alert>
          )}
          <Group justify="flex-end">
            <Button variant="default" onClick={closeForm}>
              Cancelar
            </Button>
            <Button
              color="brand"
              loading={saveMutation.isPending}
              disabled={!code.trim() || !name.trim()}
              onClick={submitForm}
            >
              Salvar
            </Button>
          </Group>
        </Stack>
      </Modal>

      <ConfirmDeleteModal
        opened={removeOpen}
        title="Excluir tipo de equipamento"
        message={`Excluir o tipo "${removeTarget?.name}"? Esta ação não pode ser desfeita.`}
        onClose={() => {
          closeRemove();
          setRemoveTarget(null);
        }}
        onConfirm={() => removeTarget && removeMutation.mutate(removeTarget.id)}
        loading={removeMutation.isPending}
        error={removeMutation.error}
        fallbackErrorMessage="Não foi possível excluir o tipo de equipamento."
      />
    </Paper>
  );
}
