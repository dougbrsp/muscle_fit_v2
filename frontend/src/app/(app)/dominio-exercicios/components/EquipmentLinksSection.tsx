'use client';

import { ActionIcon, Alert, Badge, Button, Group, Select, Stack, Text, Tooltip } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { IconAlertTriangle, IconShieldCheck, IconTrash } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { EquipmentPhoto } from '@/components/EquipmentPhoto';
import { apiFetch, ApiError } from '@/lib/api';
import type {
  EquipmentExerciseCreatePayload,
  EquipmentExerciseCreateResponse,
  EquipmentExerciseDeleteResponse,
} from '@/lib/equipment-exercises-types';
import type { ApiListResponse, ExerciseEquipmentLink } from '@/lib/exercise-catalog-admin-types';
import { PAIR_STARS_HELP } from '@/lib/equipment-recommend';
import type { Equipment } from '@/lib/gyms-types';
import { BRAND } from '@/theme';
import { ConfirmDeleteModal } from './ConfirmDeleteModal';
import { PairSafetyEditor } from './PairSafetyEditor';
import { PairStarsControl, usePairStars } from '@/components/PairStarsControl';

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

function isForbidden(error: unknown): boolean {
  return error instanceof ApiError && error.status === 403;
}

/**
 * Equipamentos vinculados a um exercício (`equipment_exercises`, refletido
 * aqui por `GET /exercises/{id}`). Escrita passa pelos endpoints canônicos
 * `POST /equipment-exercises` e `DELETE /equipment-exercises/{id}`
 * (`equipment_exercises.py`, admin-only), que usam os mesmos ids de
 * `equipment_exercises` — `link.id` serve direto no `DELETE` e no override de
 * segurança do par (`/equipment-exercises/{id}/safety`, `PairSafetyEditor`).
 *
 * Estrelas (PR-08b): nota PESSOAL do usuário corrente para o par, âncora no
 * mesmo `link.id`. Não é catálogo — fica disponível para QUALQUER usuário
 * logado, inclusive quando `isAdmin` é falso e o resto da seção é só leitura.
 */
export function EquipmentLinksSection({
  exerciseId,
  links,
  isAdmin,
  detailQueryKey,
}: {
  exerciseId: number;
  links: ExerciseEquipmentLink[];
  isAdmin: boolean;
  detailQueryKey: readonly unknown[];
}) {
  const qc = useQueryClient();

  function invalidate() {
    return qc.invalidateQueries({ queryKey: detailQueryKey });
  }

  const equipmentQuery = useQuery({
    queryKey: ['exercise-catalog-admin-equipment-catalog'],
    queryFn: () => apiFetch<ApiListResponse<Equipment>>('/equipment?active_only=true'),
    enabled: isAdmin,
    staleTime: 60_000,
  });

  const linkedEquipmentIds = new Set(links.map((link) => link.equipment_id));
  const availableEquipment = (equipmentQuery.data?.items ?? []).filter((equipment) => !linkedEquipmentIds.has(equipment.id));

  const [addOpen, { open: openAdd, close: closeAdd }] = useDisclosure(false);
  const [newEquipmentId, setNewEquipmentId] = useState<string | null>(null);

  const addMutation = useMutation({
    mutationFn: (equipmentId: number) => {
      const payload: EquipmentExerciseCreatePayload = { equipment_id: equipmentId, exercise_id: exerciseId };
      return apiFetch<EquipmentExerciseCreateResponse>('/equipment-exercises', {
        method: 'POST',
        body: JSON.stringify(payload),
      });
    },
    onSuccess: async () => {
      setNewEquipmentId(null);
      closeAdd();
      await invalidate();
    },
  });

  const pairStars = usePairStars();

  // Override de segurança: um par aberto por vez.
  const [safetyLinkId, setSafetyLinkId] = useState<number | null>(null);

  const [removeOpen, { open: openRemove, close: closeRemove }] = useDisclosure(false);
  const [removeTarget, setRemoveTarget] = useState<ExerciseEquipmentLink | null>(null);

  const removeMutation = useMutation({
    mutationFn: (link: ExerciseEquipmentLink) =>
      apiFetch<EquipmentExerciseDeleteResponse>(`/equipment-exercises/${link.id}`, {
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
      {links.length > 0 && (
        <Text size="xs" c={BRAND.textMuted}>
          {PAIR_STARS_HELP}
        </Text>
      )}
      {pairStars.query.isError && (
        <Alert color="yellow" icon={<IconAlertTriangle size={16} />}>
          {errorMessage(pairStars.query.error, 'Não foi possível carregar suas notas.')}
        </Alert>
      )}
      {pairStars.mutation.isError && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />}>
          {errorMessage(pairStars.mutation.error, 'Não foi possível salvar sua nota.')} A nota anterior foi mantida.
        </Alert>
      )}
      <Stack gap={6}>
        {links.map((link) => (
          <Stack key={link.id} gap={6}>
            <Group gap="xs" justify="space-between" wrap="wrap">
              <Group gap="xs">
                <EquipmentPhoto photoUrl={link.photo_url} alt={link.equipment_name} size={36} />
                <Text size="sm" c={BRAND.textPrimary}>
                  {link.equipment_name}
                </Text>
                {link.is_preferred ? (
                  <Badge size="xs" color="grape" variant="light">
                    preferido
                  </Badge>
                ) : null}
              </Group>
              <Group gap="sm" wrap="wrap">
              <PairStarsControl
                value={pairStars.stars.get(link.id) ?? null}
                label={link.equipment_name}
                disabled={!pairStars.query.isSuccess}
                onChange={(stars) =>
                  pairStars.mutation.mutate({
                    equipmentExerciseId: link.id,
                    equipmentId: link.equipment_id,
                    exerciseId,
                    equipmentName: link.equipment_name,
                    stars,
                  })
                }
              />
              {isAdmin ? (
                <Group gap={4} wrap="nowrap">
                  <Button
                    size="compact-xs"
                    variant={safetyLinkId === link.id ? 'filled' : 'light'}
                    color="brand"
                    leftSection={<IconShieldCheck size={14} />}
                    onClick={() => setSafetyLinkId(safetyLinkId === link.id ? null : link.id)}
                  >
                    Segurança
                  </Button>
                  <ActionIcon
                    color="red"
                    variant="subtle"
                    onClick={() => {
                      setRemoveTarget(link);
                      removeMutation.reset();
                      openRemove();
                    }}
                  >
                    <IconTrash size={16} />
                  </ActionIcon>
                </Group>
              ) : null}
              </Group>
            </Group>
            {isAdmin && safetyLinkId === link.id ? <PairSafetyEditor linkId={link.id} onClose={() => setSafetyLinkId(null)} /> : null}
          </Stack>
        ))}
        {links.length === 0 && (
          <Text size="sm" c={BRAND.textMuted}>
            Nenhum equipamento vinculado.
          </Text>
        )}
      </Stack>

      {isAdmin ? (
        addOpen ? (
          <Group align="flex-end" wrap="wrap" gap="xs">
            <Select
              label="Equipamento"
              placeholder="Selecionar"
              data={availableEquipment.map((equipment) => ({ value: String(equipment.id), label: equipment.name }))}
              value={newEquipmentId}
              onChange={setNewEquipmentId}
              searchable
              nothingFoundMessage="Nenhum equipamento disponível"
              w={240}
            />
            <Button
              color="brand"
              onClick={() => newEquipmentId && addMutation.mutate(Number(newEquipmentId))}
              disabled={!newEquipmentId}
              loading={addMutation.isPending}
            >
              Confirmar
            </Button>
            <Button
              variant="default"
              onClick={() => {
                setNewEquipmentId(null);
                closeAdd();
              }}
            >
              Cancelar
            </Button>
          </Group>
        ) : (
          <Group>
            <Button variant="light" color="brand" onClick={openAdd} disabled={availableEquipment.length === 0}>
              Associar equipamento
            </Button>
            {availableEquipment.length === 0 && equipmentQuery.isSuccess && (
              <Text size="xs" c={BRAND.textMuted}>
                Todos os equipamentos do catálogo já estão associados.
              </Text>
            )}
          </Group>
        )
      ) : (
        <Tooltip label="Ação restrita a administradores">
          <Group>
            <Button variant="light" color="brand" disabled>
              Associar equipamento
            </Button>
          </Group>
        </Tooltip>
      )}

      {addMutation.isError && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />}>
          {isForbidden(addMutation.error) ? 'Ação restrita a administradores.' : errorMessage(addMutation.error, 'Não foi possível associar o equipamento.')}
        </Alert>
      )}
      {equipmentQuery.isError && isAdmin && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />}>
          {errorMessage(equipmentQuery.error, 'Não foi possível carregar o catálogo de equipamentos.')}
        </Alert>
      )}

      <ConfirmDeleteModal
        opened={removeOpen}
        title="Remover equipamento vinculado"
        message={`Remover o equipamento "${removeTarget?.equipment_name}" deste exercício?`}
        onClose={() => {
          closeRemove();
          setRemoveTarget(null);
        }}
        onConfirm={() => removeTarget && removeMutation.mutate(removeTarget)}
        loading={removeMutation.isPending}
        error={removeMutation.error}
        fallbackErrorMessage="Não foi possível remover o equipamento vinculado."
      />
    </Stack>
  );
}
