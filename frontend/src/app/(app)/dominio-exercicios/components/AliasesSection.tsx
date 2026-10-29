'use client';

import { ActionIcon, Alert, Badge, Button, Group, Stack, Text, TextInput, Tooltip } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { IconAlertTriangle, IconTrash } from '@tabler/icons-react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { apiFetch, ApiError } from '@/lib/api';
import type { ExerciseAlias, ExerciseAliasCreateResponse, ExerciseAliasDeleteResponse } from '@/lib/exercise-catalog-admin-types';
import { BRAND } from '@/theme';
import { ConfirmDeleteModal } from './ConfirmDeleteModal';

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

function isForbidden(error: unknown): boolean {
  return error instanceof ApiError && error.status === 403;
}

/**
 * Apelidos (`exercise_aliases`) de um exercício: nomes alternativos usados
 * para reconhecer o exercício em importações/matching automático.
 */
export function AliasesSection({
  exerciseId,
  aliases,
  isAdmin,
  detailQueryKey,
}: {
  exerciseId: number;
  aliases: ExerciseAlias[];
  isAdmin: boolean;
  detailQueryKey: readonly unknown[];
}) {
  const qc = useQueryClient();
  const [newAlias, setNewAlias] = useState('');

  function invalidate() {
    return qc.invalidateQueries({ queryKey: detailQueryKey });
  }

  const addMutation = useMutation({
    mutationFn: () =>
      apiFetch<ExerciseAliasCreateResponse>(`/exercises/${exerciseId}/aliases`, {
        method: 'POST',
        body: JSON.stringify({ alias: newAlias.trim(), source: 'manual' }),
      }),
    onSuccess: async () => {
      setNewAlias('');
      await invalidate();
    },
  });

  const [removeOpen, { open: openRemove, close: closeRemove }] = useDisclosure(false);
  const [removeTarget, setRemoveTarget] = useState<ExerciseAlias | null>(null);

  const removeMutation = useMutation({
    mutationFn: (aliasId: number) => apiFetch<ExerciseAliasDeleteResponse>(`/exercises/aliases/${aliasId}`, { method: 'DELETE' }),
    onSuccess: async () => {
      closeRemove();
      setRemoveTarget(null);
      await invalidate();
    },
  });

  return (
    <Stack gap="sm">
      <Group gap={6} wrap="wrap">
        {aliases.map((alias) => (
          <Badge
            key={alias.id}
            variant="light"
            color="gray"
            rightSection={
              isAdmin ? (
                <ActionIcon
                  size="xs"
                  variant="transparent"
                  onClick={() => {
                    setRemoveTarget(alias);
                    removeMutation.reset();
                    openRemove();
                  }}
                >
                  <IconTrash size={12} />
                </ActionIcon>
              ) : undefined
            }
          >
            {alias.alias}
          </Badge>
        ))}
        {aliases.length === 0 && (
          <Text size="sm" c={BRAND.textMuted}>
            Nenhum apelido cadastrado.
          </Text>
        )}
      </Group>

      {isAdmin ? (
        <Group align="flex-end">
          <TextInput
            label="Novo apelido"
            placeholder="Ex.: Supino máquina"
            value={newAlias}
            onChange={(event) => setNewAlias(event.currentTarget.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && newAlias.trim()) addMutation.mutate();
            }}
            w={240}
          />
          <Button
            variant="light"
            color="brand"
            onClick={() => addMutation.mutate()}
            disabled={!newAlias.trim()}
            loading={addMutation.isPending}
          >
            Adicionar
          </Button>
        </Group>
      ) : (
        <Tooltip label="Ação restrita a administradores">
          <TextInput label="Novo apelido" placeholder="Ex.: Supino máquina" w={240} disabled />
        </Tooltip>
      )}

      {addMutation.isError && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />}>
          {isForbidden(addMutation.error) ? 'Ação restrita a administradores.' : errorMessage(addMutation.error, 'Não foi possível adicionar o apelido.')}
        </Alert>
      )}

      <ConfirmDeleteModal
        opened={removeOpen}
        title="Excluir apelido"
        message={`Excluir o apelido "${removeTarget?.alias}"?`}
        onClose={() => {
          closeRemove();
          setRemoveTarget(null);
        }}
        onConfirm={() => removeTarget && removeMutation.mutate(removeTarget.id)}
        loading={removeMutation.isPending}
        error={removeMutation.error}
        fallbackErrorMessage="Não foi possível excluir o apelido."
      />
    </Stack>
  );
}
