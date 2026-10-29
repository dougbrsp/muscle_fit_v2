'use client';

import { Alert, Badge, Button, Group, Modal, Stack, Switch, Table, Text, Title } from '@mantine/core';
import { useDisclosure, useMediaQuery } from '@mantine/hooks';
import { IconAlertTriangle, IconCopy, IconPlus, IconTrash } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { apiFetch, ApiError } from '@/lib/api';
import type { SessionUser } from '@/lib/auth';
import type { ApiListResponse, TrainingTypeDetail, TrainingTypeListItem } from '@/lib/training-types-types';
import { BRAND } from '@/theme';

async function fetchCurrentUser(): Promise<SessionUser> {
  return apiFetch<SessionUser>('/auth/me');
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

export function TrainingTypesListClient() {
  const router = useRouter();
  const qc = useQueryClient();
  const isMobile = useMediaQuery('(max-width: 48em)');

  // Mesma query key usada pelo AppShellLayout — o React Query dedupa.
  const currentUserQuery = useQuery({
    queryKey: ['current-user'],
    queryFn: fetchCurrentUser,
    staleTime: 60_000,
    retry: false,
  });
  const canManage = currentUserQuery.data?.role === 'admin' || currentUserQuery.data?.role === 'personal_trainer';

  const trainingTypesQuery = useQuery({
    queryKey: ['training-types'],
    queryFn: () => apiFetch<ApiListResponse<TrainingTypeListItem>>('/training-types'),
    // Router inteiro é admin/PT-only no backend (403 para os demais) — só
    // dispara depois de saber o papel do usuário, para não gastar uma
    // chamada fadada a falhar e mostrar direto a mensagem de acesso restrito.
    enabled: currentUserQuery.isSuccess,
    retry: false,
  });

  const forbidden = trainingTypesQuery.error instanceof ApiError && trainingTypesQuery.error.status === 403;

  const toggleStatusMutation = useMutation({
    mutationFn: ({ id, isActive }: { id: number; isActive: boolean }) =>
      apiFetch<TrainingTypeDetail>(`/training-types/${id}/status`, {
        method: 'PATCH',
        body: JSON.stringify({ is_active: isActive }),
      }),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ['training-types'] });
    },
  });

  const duplicateMutation = useMutation({
    mutationFn: (id: number) => apiFetch<TrainingTypeDetail>(`/training-types/${id}/duplicate`, { method: 'POST' }),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ['training-types'] });
    },
  });

  const [deleteTarget, setDeleteTarget] = useState<TrainingTypeListItem | null>(null);
  const [deleteOpened, { open: openDelete, close: closeDelete }] = useDisclosure(false);

  const deleteMutation = useMutation({
    mutationFn: (id: number) => apiFetch(`/training-types/${id}`, { method: 'DELETE' }),
    onSuccess: async () => {
      closeDelete();
      setDeleteTarget(null);
      await qc.invalidateQueries({ queryKey: ['training-types'] });
    },
  });

  function confirmDelete(trainingType: TrainingTypeListItem) {
    setDeleteTarget(trainingType);
    openDelete();
  }

  if (currentUserQuery.isSuccess && !canManage) {
    return (
      <Stack gap="md">
        <Title order={1} c={BRAND.textPrimary}>
          Metas de Ativação
        </Title>
        <Alert color="red" icon={<IconAlertTriangle size={16} />} radius="md">
          Acesso restrito a administradores e personal trainers.
        </Alert>
      </Stack>
    );
  }

  if (forbidden) {
    return (
      <Stack gap="md">
        <Title order={1} c={BRAND.textPrimary}>
          Metas de Ativação
        </Title>
        <Alert color="red" icon={<IconAlertTriangle size={16} />} radius="md">
          {errorMessage(trainingTypesQuery.error, 'Acesso restrito a administradores e personal trainers.')}
        </Alert>
      </Stack>
    );
  }

  const items = trainingTypesQuery.data?.items || [];

  return (
    <Stack gap="xl">
      <Group justify="space-between" align="flex-end" wrap="wrap">
        <div>
          <Title order={1} c={BRAND.textPrimary}>
            Metas de Ativação
          </Title>
          <Text c={BRAND.textMuted} size="md">
            Defina metas de ativação semanais de séries por grupo muscular. Associe uma meta de ativação a um modelo de treino
            para o motor de geração de ficha considerar o orçamento de volume automaticamente.
          </Text>
        </div>
        <Button
          color="brand"
          leftSection={<IconPlus size={16} />}
          onClick={() => router.push('/metas-ativacao/novo')}
        >
          Nova meta de ativação
        </Button>
      </Group>

      {(currentUserQuery.isLoading || trainingTypesQuery.isLoading) && (
        <Text c={BRAND.textMuted}>Carregando...</Text>
      )}
      {trainingTypesQuery.isError && !forbidden && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />}>
          {errorMessage(trainingTypesQuery.error, 'Não foi possível carregar as metas de ativação.')}
        </Alert>
      )}

      {trainingTypesQuery.isSuccess && (
        <Table.ScrollContainer minWidth={640}>
          <Table striped highlightOnHover withTableBorder verticalSpacing="sm">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Nome</Table.Th>
                <Table.Th>Grupos com meta</Table.Th>
                <Table.Th>Status</Table.Th>
                <Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {items.map((trainingType) => (
                <Table.Tr key={trainingType.id}>
                  <Table.Td>
                    <Text
                      fw={600}
                      c={BRAND.textPrimary}
                      style={{ cursor: 'pointer' }}
                      onClick={() => router.push(`/metas-ativacao/${trainingType.id}`)}
                    >
                      {trainingType.name}
                    </Text>
                    {trainingType.description && (
                      <Text size="xs" c={BRAND.textMuted}>
                        {trainingType.description}
                      </Text>
                    )}
                  </Table.Td>
                  <Table.Td>
                    <Badge variant="light" color="brand">
                      {trainingType.muscle_groups_count}
                    </Badge>
                  </Table.Td>
                  <Table.Td>
                    <Switch
                      color="brand"
                      checked={trainingType.is_active}
                      label={trainingType.is_active ? 'Ativo' : 'Inativo'}
                      onChange={(event) =>
                        toggleStatusMutation.mutate({ id: trainingType.id, isActive: event.currentTarget.checked })
                      }
                    />
                  </Table.Td>
                  <Table.Td>
                    <Group gap={4} justify="flex-end" wrap="nowrap">
                      <Button
                        size="xs"
                        variant="light"
                        color="brand"
                        onClick={() => router.push(`/metas-ativacao/${trainingType.id}`)}
                      >
                        Editar
                      </Button>
                      <Button
                        size="xs"
                        variant="light"
                        color="brand"
                        leftSection={<IconCopy size={14} />}
                        loading={duplicateMutation.isPending && duplicateMutation.variables === trainingType.id}
                        onClick={() => duplicateMutation.mutate(trainingType.id)}
                      >
                        Duplicar
                      </Button>
                      <Button
                        size="xs"
                        variant="light"
                        color="red"
                        leftSection={<IconTrash size={14} />}
                        onClick={() => confirmDelete(trainingType)}
                      >
                        Excluir
                      </Button>
                    </Group>
                  </Table.Td>
                </Table.Tr>
              ))}
              {items.length === 0 && (
                <Table.Tr>
                  <Table.Td colSpan={4}>
                    <Text c={BRAND.textMuted} ta="center" py="md">
                      Nenhuma meta de ativação cadastrada ainda.
                    </Text>
                  </Table.Td>
                </Table.Tr>
              )}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      )}

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
            Excluir a meta de ativação &quot;{deleteTarget?.name}&quot;? Essa ação não pode ser desfeita. Modelos
            de treino associados a ela perdem a associação (ficam sem meta de ativação), mas não são excluídos.
          </Text>
          {deleteMutation.isError && (
            <Alert color="red" icon={<IconAlertTriangle size={16} />}>
              {errorMessage(deleteMutation.error, 'Não foi possível excluir a meta de ativação.')}
            </Alert>
          )}
          <Group justify="flex-end">
            <Button variant="default" onClick={closeDelete}>
              Cancelar
            </Button>
            <Button
              color="red"
              leftSection={<IconTrash size={16} />}
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
