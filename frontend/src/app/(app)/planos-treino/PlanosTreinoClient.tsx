'use client';

import dynamic from 'next/dynamic';
import { useState } from 'react';
import Link from 'next/link';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useDisclosure } from '@mantine/hooks';
import { Alert, Badge, Button, Group, Loader, Modal, Stack, Table, Text, Title } from '@mantine/core';
import { IconAlertTriangle, IconPlus, IconTrash } from '@tabler/icons-react';
import { apiFetch, ApiError } from '@/lib/api';
import { planTypeDisplayLabel, type ApiListResponse, type WorkoutPlanListItem } from '@/lib/workout-plans-types';
import { BRAND } from '@/theme';
import { formatDateOrDash } from '@/lib/format';

// COD-14: modal sob demanda. Fechado ele não desenha nada, e o componente
// continua sempre montado, então o chunk chega logo depois da hidratação,
// antes de o usuário abrir o modal.
const GenerateDraftModal = dynamic(() => import('./GenerateDraftModal').then((m) => m.GenerateDraftModal), { ssr: false });

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

function statusColor(status: string) {
  if (status === 'completed') return 'green';
  if (status === 'skipped') return 'gray';
  return 'brand';
}

export function PlanosTreinoClient() {
  const qc = useQueryClient();
  const [generateOpened, { open: openGenerate, close: closeGenerate }] = useDisclosure(false);
  const [deleteTarget, setDeleteTarget] = useState<WorkoutPlanListItem | null>(null);
  const [deleteOpened, { open: openDelete, close: closeDelete }] = useDisclosure(false);

  const plansQuery = useQuery({
    queryKey: ['workout-plans'],
    queryFn: () => apiFetch<ApiListResponse<WorkoutPlanListItem>>('/workout-plans?limit=100'),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => apiFetch(`/workout-plans/${id}`, { method: 'DELETE' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['workout-plans'] });
      closeDelete();
      setDeleteTarget(null);
    },
  });

  function handleOpenDelete(plan: WorkoutPlanListItem) {
    setDeleteTarget(plan);
    openDelete();
  }

  const items = plansQuery.data?.items || [];

  return (
    <Stack gap="xl">
      <Group justify="space-between" align="flex-end" wrap="wrap">
        <div>
          <Title order={1} c={BRAND.textPrimary}>
            Planos de treino
          </Title>
          <Text c={BRAND.textMuted} size="md">
            Gere fichas a partir dos seus Modelos de Treino e acompanhe a execução.
          </Text>
        </div>
        <Button leftSection={<IconPlus size={16} />} color="brand" onClick={openGenerate}>
          Gerar novo plano
        </Button>
      </Group>

      {plansQuery.isLoading && (
        <Group>
          <Loader size="sm" color="brand" />
          <Text c={BRAND.textMuted}>Carregando planos...</Text>
        </Group>
      )}

      {plansQuery.isError && (
        <Alert color="red" icon={<IconAlertTriangle />} title="Erro ao carregar planos">
          {errorMessage(plansQuery.error, 'Não foi possível consultar a Fitness API.')}
        </Alert>
      )}

      {!plansQuery.isLoading && !plansQuery.isError && (
        <Table.ScrollContainer minWidth={900}>
          <Table striped highlightOnHover withTableBorder verticalSpacing="sm">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Data</Table.Th>
                <Table.Th>Nome</Table.Th>
                <Table.Th>Tipo</Table.Th>
                <Table.Th>Academia</Table.Th>
                <Table.Th>Exercícios</Table.Th>
                <Table.Th>Status</Table.Th>
                <Table.Th w={90}>Ações</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {items.map((plan) => (
                <Table.Tr key={plan.id}>
                  <Table.Td>
                    {formatDateOrDash(plan.plan_date)}
                    {plan.plan_time && (
                      <Text size="xs" c={BRAND.textMuted}>
                        {plan.plan_time}
                      </Text>
                    )}
                  </Table.Td>
                  <Table.Td>
                    <Link href={`/planos-treino/${plan.id}`} style={{ color: BRAND.orange, fontWeight: 600, textDecoration: 'none' }}>
                      {plan.name}
                    </Link>
                    {plan.goal && (
                      <Text size="xs" c={BRAND.textMuted} lineClamp={1}>
                        {plan.goal}
                      </Text>
                    )}
                  </Table.Td>
                  <Table.Td>
                    <Group gap={4}>
                      <Badge color="brand" variant="light">
                        {planTypeDisplayLabel(plan)}
                      </Badge>
                      {plan.split_day && (
                        <Badge color="brand" variant="filled">
                          {plan.split_day}
                        </Badge>
                      )}
                    </Group>
                  </Table.Td>
                  <Table.Td>{plan.gym_name || '-'}</Table.Td>
                  <Table.Td>{plan.exercises_count}</Table.Td>
                  <Table.Td>
                    <Badge color={statusColor(plan.status)} variant="light">
                      {plan.status}
                    </Badge>
                  </Table.Td>
                  <Table.Td>
                    <Button variant="subtle" color="red" size="xs" px={6} onClick={() => handleOpenDelete(plan)} aria-label="Excluir plano">
                      <IconTrash size={16} />
                    </Button>
                  </Table.Td>
                </Table.Tr>
              ))}
              {items.length === 0 && (
                <Table.Tr>
                  <Table.Td colSpan={7}>
                    <Text c={BRAND.textMuted} ta="center">
                      Nenhum plano de treino gerado ainda.
                    </Text>
                  </Table.Td>
                </Table.Tr>
              )}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      )}

      <GenerateDraftModal opened={generateOpened} onClose={closeGenerate} />

      <Modal opened={deleteOpened} onClose={closeDelete} title="Confirmar exclusão" centered>
        <Stack gap="md">
          <Text c={BRAND.textPrimary}>
            Excluir o plano{' '}
            <Text component="span" fw={700}>
              {deleteTarget?.name}
            </Text>
            ? Esta ação não pode ser desfeita.
          </Text>

          {deleteMutation.isError && (
            <Alert color="red" icon={<IconAlertTriangle />} title="Erro ao excluir">
              {errorMessage(deleteMutation.error, 'Não foi possível excluir o plano.')}
            </Alert>
          )}

          <Group justify="flex-end">
            <Button variant="default" onClick={closeDelete} disabled={deleteMutation.isPending}>
              Cancelar
            </Button>
            <Button color="red" loading={deleteMutation.isPending} onClick={() => deleteTarget && deleteMutation.mutate(deleteTarget.id)}>
              Excluir
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
