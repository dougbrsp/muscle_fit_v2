'use client';

import { ActionIcon, Alert, Badge, Button, Group, Select, Stack, Switch, Text, TextInput, Textarea, Tooltip } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { IconAlertTriangle, IconDeviceFloppy, IconPencil, IconPlus, IconTrash, IconX } from '@tabler/icons-react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { apiFetch, ApiError } from '@/lib/api';
import type {
  ExerciseExternalMapping,
  ExerciseExternalMappingUpsertResponse,
  ExerciseExternalMappingDeleteResponse,
} from '@/lib/exercise-catalog-admin-types';
import { BRAND } from '@/theme';
import { ConfirmDeleteModal } from './ConfirmDeleteModal';

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

function isForbidden(error: unknown): boolean {
  return error instanceof ApiError && error.status === 403;
}

// 'AMAZFIT' é o único provider observado em uso real hoje — o backend não
// valida um enum fixo (campo livre), então mantemos como sugestão/padrão em
// vez de restringir a um Select fechado.
const PROVIDER_SUGGESTIONS = [{ value: 'AMAZFIT', label: 'AMAZFIT' }];

// Status reais aceitos pelo backend (`exercise_external_mappings.mapping_status`
// não tem enum no schema, mas o CRUD sempre grava um destes três). "PENDING"
// não existe mais como opção válida — era usado só pela extinta tela
// `/mapeamentos` do onpremise; dado legado salvo como "PENDING" só é exibido
// como está, sem quebrar, até ser corrigido para um dos três valores abaixo.
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

function isTruthy(value: number | boolean | null | undefined): boolean {
  return value === true || value === 1;
}

type FormDraft = {
  provider: string;
  external_id: string;
  external_name: string;
  mapping_status: string;
  source: string;
  notes: string;
  is_active: boolean;
};

const emptyDraft: FormDraft = {
  provider: 'AMAZFIT',
  external_id: '',
  external_name: '',
  mapping_status: 'CONFIRMED',
  source: '',
  notes: '',
  is_active: true,
};

function draftFrom(mapping: ExerciseExternalMapping): FormDraft {
  return {
    provider: mapping.provider || 'AMAZFIT',
    external_id: mapping.external_id || '',
    external_name: mapping.external_name || '',
    mapping_status: MAPPING_STATUS_OPTIONS.some((option) => option.value === (mapping.mapping_status || '').toUpperCase())
      ? (mapping.mapping_status || '').toUpperCase()
      : 'CONFIRMED',
    source: mapping.source || '',
    notes: mapping.notes || '',
    is_active: isTruthy(mapping.is_active),
  };
}

/**
 * Mapeamentos externos (`exercise_external_mappings`) de um exercício:
 * associação a um nome/id usado por um provider externo (ex.: nome do
 * exercício no Amazfit/Zepp), alimentando o matching automático de
 * `/mapping-canonical`. Edição completa (status real de 3 valores,
 * external_id, source, notes, is_active) fica inline por mapeamento; a
 * mesma listagem, sem o recorte por exercício, também aparece na aba
 * "Mapeamentos externos" de `/admin/dominio-exercicios`
 * (`ExternalMappingsPanel.tsx`).
 */
export function ExternalMappingsSection({
  exerciseId,
  mappings,
  isAdmin,
  detailQueryKey,
}: {
  exerciseId: number;
  mappings: ExerciseExternalMapping[];
  isAdmin: boolean;
  detailQueryKey: readonly unknown[];
}) {
  const qc = useQueryClient();

  function invalidate() {
    return qc.invalidateQueries({ queryKey: detailQueryKey });
  }

  // ---- edição inline por mapeamento -----------------------------------
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editDraft, setEditDraft] = useState<FormDraft | null>(null);

  function startEdit(mapping: ExerciseExternalMapping) {
    setEditingId(mapping.id);
    setEditDraft(draftFrom(mapping));
    saveMutation.reset();
  }

  function cancelEdit() {
    setEditingId(null);
    setEditDraft(null);
  }

  function updateEditDraft<K extends keyof FormDraft>(field: K, value: FormDraft[K]) {
    setEditDraft((current) => (current ? { ...current, [field]: value } : current));
  }

  const saveMutation = useMutation({
    mutationFn: () => {
      if (editingId === null || !editDraft) throw new Error('Nenhum mapeamento em edição.');
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
      await invalidate();
    },
  });

  // ---- exclusão ------------------------------------------------------------
  const [removeOpen, { open: openRemove, close: closeRemove }] = useDisclosure(false);
  const [removeTarget, setRemoveTarget] = useState<ExerciseExternalMapping | null>(null);

  const removeMutation = useMutation({
    mutationFn: (id: number) => apiFetch<ExerciseExternalMappingDeleteResponse>(`/exercises/external-mappings/${id}`, { method: 'DELETE' }),
    onSuccess: async () => {
      closeRemove();
      setRemoveTarget(null);
      await invalidate();
    },
  });

  // ---- criação --------------------------------------------------------------
  const [createOpen, { open: openCreate, close: closeCreateRaw }] = useDisclosure(false);
  const [createDraft, setCreateDraft] = useState<FormDraft>({ ...emptyDraft });

  function closeCreate() {
    closeCreateRaw();
    setCreateDraft({ ...emptyDraft });
    addMutation.reset();
  }

  function updateCreateDraft<K extends keyof FormDraft>(field: K, value: FormDraft[K]) {
    setCreateDraft((current) => ({ ...current, [field]: value }));
  }

  const addMutation = useMutation({
    mutationFn: () =>
      apiFetch<ExerciseExternalMappingUpsertResponse>(`/exercises/${exerciseId}/external-mappings`, {
        method: 'POST',
        body: JSON.stringify({
          provider: createDraft.provider.trim() || 'AMAZFIT',
          external_id: createDraft.external_id.trim() || null,
          external_name: createDraft.external_name.trim(),
          mapping_status: createDraft.mapping_status,
          source: createDraft.source.trim() || null,
          notes: createDraft.notes.trim() || null,
          is_active: createDraft.is_active,
        }),
      }),
    onSuccess: async () => {
      closeCreate();
      await invalidate();
    },
  });

  return (
    <Stack gap="sm">
      {mappings.map((mapping) => {
        const editing = editingId === mapping.id && editDraft;

        if (editing && editDraft) {
          return (
            <Stack key={mapping.id} gap="xs" p="sm" style={{ border: `1px solid ${BRAND.border}`, borderRadius: 8 }}>
              <Group grow align="start">
                <TextInput
                  size="xs"
                  label="External ID"
                  value={editDraft.external_id}
                  onChange={(event) => updateEditDraft('external_id', event.currentTarget.value)}
                />
                <TextInput
                  size="xs"
                  label="Nome externo"
                  value={editDraft.external_name}
                  onChange={(event) => updateEditDraft('external_name', event.currentTarget.value)}
                  required
                />
              </Group>
              <Group grow align="start">
                <Select
                  size="xs"
                  label="Status"
                  data={MAPPING_STATUS_OPTIONS}
                  value={editDraft.mapping_status}
                  onChange={(value) => updateEditDraft('mapping_status', value || 'CONFIRMED')}
                  allowDeselect={false}
                />
                <TextInput
                  size="xs"
                  label="Fonte"
                  value={editDraft.source}
                  onChange={(event) => updateEditDraft('source', event.currentTarget.value)}
                />
              </Group>
              <Textarea
                size="xs"
                label="Notas"
                minRows={2}
                value={editDraft.notes}
                onChange={(event) => updateEditDraft('notes', event.currentTarget.value)}
              />
              <Switch
                label="Ativo"
                checked={editDraft.is_active}
                onChange={(event) => updateEditDraft('is_active', event.currentTarget.checked)}
              />
              {saveMutation.isError && (
                <Alert color="red" icon={<IconAlertTriangle size={16} />}>
                  {isForbidden(saveMutation.error) ? 'Ação restrita a administradores.' : errorMessage(saveMutation.error, 'Não foi possível salvar o mapeamento.')}
                </Alert>
              )}
              <Group gap="xs" justify="flex-end">
                <Button size="xs" variant="subtle" color="gray" leftSection={<IconX size={14} />} onClick={cancelEdit}>
                  Cancelar
                </Button>
                <Button
                  size="xs"
                  color="brand"
                  leftSection={<IconDeviceFloppy size={14} />}
                  loading={saveMutation.isPending}
                  disabled={!editDraft.external_name.trim()}
                  onClick={() => saveMutation.mutate()}
                >
                  Salvar
                </Button>
              </Group>
            </Stack>
          );
        }

        return (
          <Group key={mapping.id} justify="space-between" wrap="wrap" opacity={isTruthy(mapping.is_active) ? 1 : 0.55}>
            <Stack gap={2}>
              <Group gap="xs">
                <Badge size="sm" variant="light" color="brand">
                  {mapping.provider}
                </Badge>
                <Text size="sm" c={BRAND.textPrimary}>
                  {mapping.external_name}
                </Text>
                <Badge size="xs" color={statusColor(mapping.mapping_status)} variant="light">
                  {mapping.mapping_status || '-'}
                </Badge>
                <Badge size="xs" color={isTruthy(mapping.is_active) ? 'green' : 'gray'} variant="light">
                  {isTruthy(mapping.is_active) ? 'ativo' : 'inativo'}
                </Badge>
              </Group>
              {(mapping.external_id || mapping.source || mapping.notes) && (
                <Text size="xs" c={BRAND.textMuted}>
                  {[
                    mapping.external_id ? `ID: ${mapping.external_id}` : null,
                    mapping.source ? `Fonte: ${mapping.source}` : null,
                    mapping.notes ? `Notas: ${mapping.notes}` : null,
                  ]
                    .filter(Boolean)
                    .join(' · ')}
                </Text>
              )}
            </Stack>
            {isAdmin ? (
              <Group gap={4} wrap="nowrap">
                <Tooltip label="Editar">
                  <ActionIcon variant="subtle" color="brand" onClick={() => startEdit(mapping)}>
                    <IconPencil size={16} />
                  </ActionIcon>
                </Tooltip>
                <Tooltip label="Excluir">
                  <ActionIcon
                    color="red"
                    variant="subtle"
                    onClick={() => {
                      setRemoveTarget(mapping);
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
                <ActionIcon color="red" variant="subtle" disabled>
                  <IconTrash size={16} />
                </ActionIcon>
              </Tooltip>
            )}
          </Group>
        );
      })}
      {mappings.length === 0 && (
        <Text size="sm" c={BRAND.textMuted}>
          Nenhum mapeamento externo cadastrado.
        </Text>
      )}

      {isAdmin && !createOpen && (
        <Button variant="light" color="brand" leftSection={<IconPlus size={16} />} onClick={openCreate} style={{ alignSelf: 'flex-start' }}>
          Adicionar mapeamento externo
        </Button>
      )}

      {isAdmin && createOpen && (
        <Stack gap="xs" p="sm" style={{ border: `1px dashed ${BRAND.border}`, borderRadius: 8 }}>
          <Group grow align="start">
            <Select
              size="xs"
              label="Provider"
              data={PROVIDER_SUGGESTIONS}
              value={createDraft.provider}
              onChange={(value) => updateCreateDraft('provider', value || 'AMAZFIT')}
              searchable
              allowDeselect={false}
            />
            <TextInput
              size="xs"
              label="External ID"
              value={createDraft.external_id}
              onChange={(event) => updateCreateDraft('external_id', event.currentTarget.value)}
            />
          </Group>
          <TextInput
            size="xs"
            label="Nome externo"
            placeholder="Ex.: nome usado pelo Amazfit/Zepp"
            value={createDraft.external_name}
            onChange={(event) => updateCreateDraft('external_name', event.currentTarget.value)}
            required
          />
          <Group grow align="start">
            <Select
              size="xs"
              label="Status"
              data={MAPPING_STATUS_OPTIONS}
              value={createDraft.mapping_status}
              onChange={(value) => updateCreateDraft('mapping_status', value || 'CONFIRMED')}
              allowDeselect={false}
            />
            <TextInput
              size="xs"
              label="Fonte"
              value={createDraft.source}
              onChange={(event) => updateCreateDraft('source', event.currentTarget.value)}
            />
          </Group>
          <Textarea
            size="xs"
            label="Notas"
            minRows={2}
            value={createDraft.notes}
            onChange={(event) => updateCreateDraft('notes', event.currentTarget.value)}
          />
          <Switch
            label="Ativo"
            checked={createDraft.is_active}
            onChange={(event) => updateCreateDraft('is_active', event.currentTarget.checked)}
          />
          {addMutation.isError && (
            <Alert color="red" icon={<IconAlertTriangle size={16} />}>
              {isForbidden(addMutation.error) ? 'Ação restrita a administradores.' : errorMessage(addMutation.error, 'Não foi possível adicionar o mapeamento externo.')}
            </Alert>
          )}
          <Group gap="xs" justify="flex-end">
            <Button size="xs" variant="subtle" color="gray" onClick={closeCreate}>
              Cancelar
            </Button>
            <Button
              size="xs"
              color="brand"
              loading={addMutation.isPending}
              disabled={!createDraft.external_name.trim()}
              onClick={() => addMutation.mutate()}
            >
              Adicionar
            </Button>
          </Group>
        </Stack>
      )}

      <ConfirmDeleteModal
        opened={removeOpen}
        title="Excluir mapeamento externo"
        message={`Excluir o mapeamento "${removeTarget?.provider}: ${removeTarget?.external_name}"?`}
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
