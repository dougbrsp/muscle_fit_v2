'use client';

import { ActionIcon, Alert, Badge, Button, Group, NumberInput, Select, Stack, Text, Tooltip } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { IconAlertTriangle, IconPencil, IconTrash } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { apiFetch, ApiError } from '@/lib/api';
import { defaultWeightsForRole } from '@/lib/muscle-contribution-weights';
import type {
  ApiListResponse,
  ExerciseMuscleContribution,
  MuscleContributionUpsertPayload,
  MuscleContributionUpsertResponse,
  MuscleContributionDeactivateResponse,
  MuscleGroupOption,
} from '@/lib/exercise-catalog-admin-types';
import { BRAND } from '@/theme';
import { ConfirmDeleteModal } from './ConfirmDeleteModal';

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

function isForbidden(error: unknown): boolean {
  return error instanceof ApiError && error.status === 403;
}

const ROLE_OPTIONS = [
  { value: 'PRIMARY', label: 'Primário' },
  { value: 'SECONDARY', label: 'Secundário' },
  { value: 'STABILIZER', label: 'Estabilizador' },
];

function roleColor(role: string): string {
  if (role === 'PRIMARY') return 'blue';
  if (role === 'SECONDARY') return 'orange';
  return 'gray';
}

function roleLabel(role: string): string {
  return ROLE_OPTIONS.find((option) => option.value === role)?.label ?? role;
}

/**
 * Contribuições musculares (`exercise_muscle_contributions`) de um
 * exercício: papel (PRIMARY/SECONDARY/STABILIZER) e pesos de
 * estímulo/fadiga por grupamento, usados por `VolumeService`. CRUD via
 * `muscle_contributions_router` (`PUT`/`DELETE /muscle-contributions/{mapping_id}/{muscle_group_id}`,
 * `exercise_mapping_catalog.py`) — `GET` exige apenas autenticação, escrita
 * exige admin.
 */
export function MuscleContributionsSection({
  exerciseId,
  contributions,
  isAdmin,
  detailQueryKey,
}: {
  exerciseId: number;
  contributions: ExerciseMuscleContribution[];
  isAdmin: boolean;
  detailQueryKey: readonly unknown[];
}) {
  const qc = useQueryClient();

  function invalidate() {
    return qc.invalidateQueries({ queryKey: detailQueryKey });
  }

  const muscleGroupsQuery = useQuery({
    queryKey: ['exercise-catalog-admin-muscle-groups'],
    queryFn: () => apiFetch<ApiListResponse<MuscleGroupOption>>('/muscle-groups'),
    enabled: isAdmin,
    staleTime: 60_000,
  });

  const assignedGroupIds = new Set(contributions.map((c) => c.muscle_group_id));
  const availableGroups = (muscleGroupsQuery.data?.items ?? []).filter((group) => !assignedGroupIds.has(group.id));

  const upsertMutation = useMutation({
    mutationFn: ({ muscleGroupId, payload }: { muscleGroupId: number; payload: MuscleContributionUpsertPayload }) =>
      apiFetch<MuscleContributionUpsertResponse>(`/muscle-contributions/${exerciseId}/${muscleGroupId}`, {
        method: 'PUT',
        body: JSON.stringify(payload),
      }),
    onSuccess: async () => {
      await invalidate();
    },
  });

  const [addOpen, { open: openAdd, close: closeAdd }] = useDisclosure(false);
  const [newGroupId, setNewGroupId] = useState<string | null>(null);
  const [newRole, setNewRole] = useState<'PRIMARY' | 'SECONDARY' | 'STABILIZER'>('PRIMARY');
  const [newStimulus, setNewStimulus] = useState<number>(1);
  const [newFatigue, setNewFatigue] = useState<number>(1);

  function resetAddForm() {
    setNewGroupId(null);
    setNewRole('PRIMARY');
    setNewStimulus(1);
    setNewFatigue(1);
  }

  function confirmAdd() {
    if (!newGroupId) return;
    upsertMutation.mutate(
      {
        muscleGroupId: Number(newGroupId),
        payload: { role: newRole, stimulus_weight: newStimulus, fatigue_weight: newFatigue },
      },
      {
        onSuccess: () => {
          resetAddForm();
          closeAdd();
        },
      },
    );
  }

  const [editingGroupId, setEditingGroupId] = useState<number | null>(null);
  const [editingRole, setEditingRole] = useState<'PRIMARY' | 'SECONDARY' | 'STABILIZER'>('PRIMARY');

  function confirmRoleEdit(contribution: ExerciseMuscleContribution) {
    upsertMutation.mutate(
      {
        muscleGroupId: contribution.muscle_group_id,
        payload: {
          role: editingRole,
          stimulus_weight: contribution.stimulus_weight,
          fatigue_weight: contribution.fatigue_weight,
        },
      },
      { onSuccess: () => setEditingGroupId(null) },
    );
  }

  const [removeOpen, { open: openRemove, close: closeRemove }] = useDisclosure(false);
  const [removeTarget, setRemoveTarget] = useState<ExerciseMuscleContribution | null>(null);

  const removeMutation = useMutation({
    mutationFn: (muscleGroupId: number) =>
      apiFetch<MuscleContributionDeactivateResponse>(`/muscle-contributions/${exerciseId}/${muscleGroupId}`, {
        method: 'DELETE',
      }),
    onSuccess: async () => {
      closeRemove();
      setRemoveTarget(null);
      await invalidate();
    },
  });

  return (
    <Stack gap="sm">
      <Stack gap={6}>
        {contributions.map((contribution) => (
          <Group key={contribution.id} gap="xs" justify="space-between" wrap="wrap">
            <Group gap="xs">
              <Text size="sm" c={BRAND.textPrimary}>
                {contribution.muscle_group_name}
              </Text>
              {editingGroupId === contribution.muscle_group_id ? (
                <Group gap={4}>
                  <Select
                    size="xs"
                    data={ROLE_OPTIONS}
                    value={editingRole}
                    onChange={(value) => setEditingRole((value as typeof editingRole) || 'PRIMARY')}
                    allowDeselect={false}
                    w={140}
                  />
                  <Button size="xs" color="brand" loading={upsertMutation.isPending} onClick={() => confirmRoleEdit(contribution)}>
                    Confirmar
                  </Button>
                  <Button size="xs" variant="default" onClick={() => setEditingGroupId(null)}>
                    Cancelar
                  </Button>
                </Group>
              ) : (
                <Badge size="xs" color={roleColor(contribution.role)} variant="light">
                  {roleLabel(contribution.role)}
                </Badge>
              )}
              <Text size="xs" c={BRAND.textMuted}>
                estímulo {contribution.stimulus_weight} · fadiga {contribution.fatigue_weight}
              </Text>
            </Group>
            {isAdmin && editingGroupId !== contribution.muscle_group_id ? (
              <Group gap={4}>
                <ActionIcon
                  variant="subtle"
                  color="brand"
                  onClick={() => {
                    setEditingGroupId(contribution.muscle_group_id);
                    setEditingRole(contribution.role);
                  }}
                >
                  <IconPencil size={16} />
                </ActionIcon>
                <ActionIcon
                  color="red"
                  variant="subtle"
                  onClick={() => {
                    setRemoveTarget(contribution);
                    removeMutation.reset();
                    openRemove();
                  }}
                >
                  <IconTrash size={16} />
                </ActionIcon>
              </Group>
            ) : null}
          </Group>
        ))}
        {contributions.length === 0 && (
          <Text size="sm" c={BRAND.textMuted}>
            Nenhuma contribuição muscular cadastrada.
          </Text>
        )}
      </Stack>

      {isAdmin && (
        <>
          {addOpen ? (
            <Group align="flex-end" wrap="wrap" gap="xs">
              <Select
                label="Grupo muscular"
                placeholder="Selecionar"
                data={availableGroups.map((group) => ({ value: String(group.id), label: group.name }))}
                value={newGroupId}
                onChange={setNewGroupId}
                searchable
                nothingFoundMessage="Nenhum grupo disponível"
                w={200}
              />
              <Select
                label="Role"
                data={ROLE_OPTIONS}
                value={newRole}
                onChange={(value) => {
                  const role = (value as typeof newRole) || 'PRIMARY';
                  setNewRole(role);
                  const defaults = defaultWeightsForRole(role);
                  setNewStimulus(defaults.stimulus);
                  setNewFatigue(defaults.fatigue);
                }}
                allowDeselect={false}
                w={150}
              />
              <NumberInput
                label="Estímulo"
                value={newStimulus}
                onChange={(value) => setNewStimulus(typeof value === 'number' ? value : 0)}
                min={0}
                max={2}
                step={0.05}
                decimalScale={2}
                w={100}
              />
              <NumberInput
                label="Fadiga"
                value={newFatigue}
                onChange={(value) => setNewFatigue(typeof value === 'number' ? value : 0)}
                min={0}
                max={2}
                step={0.05}
                decimalScale={2}
                w={100}
              />
              <Button color="brand" onClick={confirmAdd} disabled={!newGroupId} loading={upsertMutation.isPending}>
                Confirmar
              </Button>
              <Button
                variant="default"
                onClick={() => {
                  resetAddForm();
                  closeAdd();
                }}
              >
                Cancelar
              </Button>
            </Group>
          ) : (
            <Group>
              <Button variant="light" color="brand" onClick={openAdd} disabled={availableGroups.length === 0}>
                Adicionar
              </Button>
              {availableGroups.length === 0 && muscleGroupsQuery.isSuccess && (
                <Text size="xs" c={BRAND.textMuted}>
                  Todos os grupos musculares já estão atribuídos.
                </Text>
              )}
            </Group>
          )}
        </>
      )}

      {!isAdmin && (
        <Tooltip label="Ação restrita a administradores">
          <Group>
            <Button variant="light" color="brand" disabled>
              Adicionar
            </Button>
          </Group>
        </Tooltip>
      )}

      {upsertMutation.isError && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />}>
          {isForbidden(upsertMutation.error)
            ? 'Ação restrita a administradores.'
            : errorMessage(upsertMutation.error, 'Não foi possível salvar a contribuição muscular.')}
        </Alert>
      )}
      {muscleGroupsQuery.isError && isAdmin && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />}>
          {errorMessage(muscleGroupsQuery.error, 'Não foi possível carregar os grupos musculares.')}
        </Alert>
      )}

      <ConfirmDeleteModal
        opened={removeOpen}
        title="Excluir contribuição muscular"
        message={`Excluir a contribuição do grupo "${removeTarget?.muscle_group_name}"?`}
        onClose={() => {
          closeRemove();
          setRemoveTarget(null);
        }}
        onConfirm={() => removeTarget && removeMutation.mutate(removeTarget.muscle_group_id)}
        loading={removeMutation.isPending}
        error={removeMutation.error}
        fallbackErrorMessage="Não foi possível excluir a contribuição muscular."
      />
    </Stack>
  );
}
