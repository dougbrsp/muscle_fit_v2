'use client';

import { Alert, Button, Divider, Group, Modal, MultiSelect, Stack, Table, Text } from '@mantine/core';
import { IconAlertTriangle, IconTrash } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { apiFetch, ApiError } from '@/lib/api';
// Reusada de `/indicadores-fisicos`: recorte de string puro, sem `new Date()`
// - uma data vinda do banco nao pode escorregar um dia por fuso do browser.
import { formatDateBR } from '@/lib/physical-indicators-types';
import { fetchUserLookup, userLookupKey, userLookupLabel } from '@/lib/user-lookup';
import type {
  ApiListResponse,
  WorkoutPlanTemplateAssignPayload,
  WorkoutPlanTemplateAssignment,
} from '@/lib/workout-plan-templates-types';
import { BRAND } from '@/theme';

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

type Props = {
  templateId: number;
  opened: boolean;
  onClose: () => void;
  onAssigned?: () => void;
};

/**
 * Modal de atribuição de um modelo de treino a usuários — `GET
 * /workout-plan-templates/users-lookup` para o multi-select (lookup mínimo,
 * evita expor `GET /users` admin-only puro a personal trainers) e `POST
 * /workout-plan-templates/{id}/assignments` por usuário selecionado.
 *
 * M23: um usuário só segue UM modelo por vez. Atribuir este modelo a alguém
 * ENCERRA o modelo que essa pessoa seguia — o `POST` faz a troca inteira, e
 * o aviso no topo do modal diz isso antes do clique, porque quem atribui
 * precisa saber que está substituindo, não somando.
 *
 * A lista de baixo passa a ter dois blocos, vindos da MESMA chamada
 * (`?include_history=1`): quem segue este modelo hoje e quem já seguiu. O
 * histórico é somente leitura — ele existe para as evoluções feitas sob este
 * modelo continuarem explicáveis depois da troca, que é a metade "sem perder
 * as evoluções" do pedido. Por isso o botão deixou de ser "Remover" (DELETE
 * físico, que apagava o passado) e passou a ser "Encerrar".
 */
export function AssignTemplateModal({ templateId, opened, onClose, onAssigned }: Props) {
  const qc = useQueryClient();
  const [search, setSearch] = useState('');
  const [selectedUserIds, setSelectedUserIds] = useState<string[]>([]);

  const assignmentsQuery = useQuery({
    queryKey: ['workout-plan-template-assignments', templateId],
    queryFn: () =>
      apiFetch<ApiListResponse<WorkoutPlanTemplateAssignment>>(
        `/workout-plan-templates/${templateId}/assignments?include_history=1`,
      ),
    enabled: opened,
  });

  const usersQuery = useQuery({
    queryKey: userLookupKey(search),
    queryFn: () => fetchUserLookup(search),
    enabled: opened,
  });

  const assignMutation = useMutation({
    mutationFn: async () => {
      for (const userId of selectedUserIds) {
        const payload: WorkoutPlanTemplateAssignPayload = { user_id: Number(userId) };
        await apiFetch(`/workout-plan-templates/${templateId}/assignments`, {
          method: 'POST',
          body: JSON.stringify(payload),
        });
      }
    },
    onSuccess: async () => {
      setSelectedUserIds([]);
      await qc.invalidateQueries({ queryKey: ['workout-plan-template-assignments', templateId] });
      onAssigned?.();
    },
  });

  const removeMutation = useMutation({
    mutationFn: (userId: number) =>
      apiFetch(`/workout-plan-templates/${templateId}/assignments/${userId}`, { method: 'DELETE' }),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ['workout-plan-template-assignments', templateId] });
      onAssigned?.();
    },
  });

  function handleClose() {
    setSelectedUserIds([]);
    setSearch('');
    onClose();
  }

  const userOptions = (usersQuery.data?.items || []).map((user) => ({
    value: String(user.id),
    label: userLookupLabel(user),
  }));
  const allAssignments = assignmentsQuery.data?.items || [];
  const assignments = allAssignments.filter((a) => a.unassigned_at === null);
  const pastAssignments = allAssignments.filter((a) => a.unassigned_at !== null);

  return (
    <Modal opened={opened} onClose={handleClose} title="Atribuir modelo a usuários" size="md">
      <Stack gap="md">
        <Text size="sm" c={BRAND.textMuted}>
          Selecione os usuários que devem receber este modelo. O motor de geração de ficha passa a usar este modelo
          para eles automaticamente.
        </Text>
        <Alert color="yellow" icon={<IconAlertTriangle size={16} />}>
          Cada usuário segue <strong>um modelo por vez</strong>. Atribuir este modelo encerra o modelo que a pessoa
          seguia — o histórico é preservado, e as evoluções feitas com o modelo anterior continuam no dashboard.
        </Alert>
        <MultiSelect
          label="Usuários"
          placeholder="Buscar por nome ou usuário..."
          data={userOptions}
          value={selectedUserIds}
          onChange={setSelectedUserIds}
          searchable
          searchValue={search}
          onSearchChange={setSearch}
          nothingFoundMessage={usersQuery.isLoading ? 'Carregando...' : 'Nenhum usuário encontrado.'}
        />
        {assignMutation.isError && (
          <Alert color="red" icon={<IconAlertTriangle size={16} />}>
            {errorMessage(assignMutation.error, 'Não foi possível atribuir o modelo.')}
          </Alert>
        )}
        <Group justify="flex-end">
          <Button
            color="brand"
            onClick={() => assignMutation.mutate()}
            loading={assignMutation.isPending}
            disabled={selectedUserIds.length === 0}
          >
            Atribuir
          </Button>
        </Group>

        <Divider label="Seguindo este modelo hoje" />

        {assignmentsQuery.isLoading && <Text c={BRAND.textMuted}>Carregando...</Text>}
        {assignmentsQuery.isError && (
          <Alert color="red" icon={<IconAlertTriangle size={16} />}>
            {errorMessage(assignmentsQuery.error, 'Não foi possível carregar as atribuições.')}
          </Alert>
        )}
        {assignmentsQuery.isSuccess && (
          <Table verticalSpacing="xs">
            <Table.Tbody>
              {assignments.map((assignment) => (
                <Table.Tr key={assignment.id}>
                  <Table.Td c={BRAND.textPrimary}>
                    {assignment.full_name ? `${assignment.full_name} (${assignment.username})` : assignment.username}
                  </Table.Td>
                  <Table.Td>
                    <Button
                      size="xs"
                      variant="subtle"
                      color="red"
                      leftSection={<IconTrash size={12} />}
                      loading={removeMutation.isPending && removeMutation.variables === assignment.user_id}
                      onClick={() => removeMutation.mutate(assignment.user_id)}
                    >
                      Encerrar
                    </Button>
                  </Table.Td>
                </Table.Tr>
              ))}
              {assignments.length === 0 && (
                <Table.Tr>
                  <Table.Td colSpan={2}>
                    <Text c={BRAND.textMuted} ta="center" py="sm">
                      Nenhum usuário segue este modelo hoje.
                    </Text>
                  </Table.Td>
                </Table.Tr>
              )}
            </Table.Tbody>
          </Table>
        )}

        {pastAssignments.length > 0 && (
          <>
            <Divider label="Já seguiram este modelo" />
            <Table verticalSpacing="xs">
              <Table.Tbody>
                {pastAssignments.map((assignment) => (
                  <Table.Tr key={assignment.id}>
                    <Table.Td c={BRAND.textMuted}>
                      {assignment.full_name
                        ? `${assignment.full_name} (${assignment.username})`
                        : assignment.username}
                    </Table.Td>
                    <Table.Td c={BRAND.textMuted} ta="right">
                      <Text size="xs">
                        {formatDateBR(assignment.assigned_at)} — {formatDateBR(assignment.unassigned_at)}
                      </Text>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </>
        )}

        <Group justify="flex-end">
          <Button variant="default" onClick={handleClose}>
            Fechar
          </Button>
        </Group>
      </Stack>
    </Modal>
  );
}
