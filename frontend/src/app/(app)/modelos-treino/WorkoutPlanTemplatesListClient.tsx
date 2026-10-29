'use client';

import dynamic from 'next/dynamic';
import { Alert, Badge, Button, Group, Modal, Stack, Switch, Table, Text, Title } from '@mantine/core';
import { useDisclosure, useMediaQuery } from '@mantine/hooks';
import {
  IconAlertTriangle,
  IconChartBar,
  IconCopy,
  IconPlus,
  IconSparkles,
  IconTrash,
  IconUsers,
} from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { apiFetch, ApiError } from '@/lib/api';
import type { SessionUser } from '@/lib/auth';
import {
  VOLUME_MODE_OPTIONS,
  type ApiListResponse,
  type WorkoutPlanTemplateDetail,
  type WorkoutPlanTemplateListItem,
} from '@/lib/workout-plan-templates-types';
import { BRAND } from '@/theme';
import { TrainingGoalBadge } from '@/components/TrainingGoalBadge';
import { AssignTemplateModal } from './AssignTemplateModal';
import { versionLabel } from '@/lib/template-versions';

// COD-14: modal sob demanda. Fechado ele não desenha nada, e o componente
// continua sempre montado, então o chunk chega logo depois da hidratação,
// antes de o usuário abrir o modal.
const PeriodizationModal = dynamic(() => import('./PeriodizationModal').then((m) => m.PeriodizationModal), { ssr: false });

async function fetchCurrentUser(): Promise<SessionUser> {
  return apiFetch<SessionUser>('/auth/me');
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

function volumeModeLabel(mode: string): string {
  return VOLUME_MODE_OPTIONS.find((option) => option.value === mode)?.label || mode;
}

/**
 * Listagem de "Modelos de treino" (Fase 4, Etapa 15). Mesmo padrão
 * estrutural de TrainingTypesListClient.tsx (Etapa 14): router inteiro é
 * admin/PT-only no backend (403 para os demais), então a query só dispara
 * depois de `/auth/me` resolver o papel do usuário.
 *
 * Gap documentado (fora de escopo desta etapa): `GET
 * /workout-plan-templates/assigned-to-me` (me_router, aberto a qualquer
 * usuário autenticado) não é exibido aqui — é consumido hoje só pelo popup
 * "Gerar novo plano" de /planos-treino (Etapa 13), fluxo que esta etapa não
 * deve alterar. Um usuário comum não vê "Modelos de treino" na navegação
 * (item de nav restrito a admin/PT) nem tem acesso a este CRUD.
 */
export function WorkoutPlanTemplatesListClient() {
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

  const templatesQuery = useQuery({
    queryKey: ['workout-plan-templates'],
    queryFn: () => apiFetch<ApiListResponse<WorkoutPlanTemplateListItem>>('/workout-plan-templates'),
    enabled: currentUserQuery.isSuccess,
    retry: false,
  });

  const forbidden = templatesQuery.error instanceof ApiError && templatesQuery.error.status === 403;

  const toggleStatusMutation = useMutation({
    mutationFn: ({ id, isActive }: { id: number; isActive: boolean }) =>
      apiFetch<WorkoutPlanTemplateDetail>(`/workout-plan-templates/${id}/status`, {
        method: 'PATCH',
        body: JSON.stringify({ is_active: isActive }),
      }),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ['workout-plan-templates'] });
    },
  });

  const duplicateMutation = useMutation({
    mutationFn: (id: number) =>
      apiFetch<WorkoutPlanTemplateDetail>(`/workout-plan-templates/${id}/duplicate`, { method: 'POST' }),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ['workout-plan-templates'] });
    },
  });

  const [deleteTarget, setDeleteTarget] = useState<WorkoutPlanTemplateListItem | null>(null);
  const [deleteOpened, { open: openDelete, close: closeDelete }] = useDisclosure(false);

  const deleteMutation = useMutation({
    mutationFn: (id: number) => apiFetch(`/workout-plan-templates/${id}`, { method: 'DELETE' }),
    onSuccess: async () => {
      closeDelete();
      setDeleteTarget(null);
      await qc.invalidateQueries({ queryKey: ['workout-plan-templates'] });
    },
  });

  function confirmDelete(template: WorkoutPlanTemplateListItem) {
    setDeleteTarget(template);
    openDelete();
  }

  // Modal do Motor de Periodização (PR-17). O botão que o abre só existe para
  // admin/PT — o mesmo `require_admin_or_pt` que protege
  // `POST /workout-plan-templates/periodization-drafts` no backend (D17); a
  // tela inteira já é admin/PT-only, então o gate aqui é de UI, não de
  // segurança.
  const [periodizationOpened, { open: openPeriodization, close: closePeriodization }] = useDisclosure(false);

  const [assignTarget, setAssignTarget] = useState<WorkoutPlanTemplateListItem | null>(null);
  const [assignOpened, { open: openAssign, close: closeAssign }] = useDisclosure(false);

  function openAssignModal(template: WorkoutPlanTemplateListItem) {
    setAssignTarget(template);
    openAssign();
  }

  if (currentUserQuery.isSuccess && !canManage) {
    return (
      <Stack gap="md">
        <Title order={1} c={BRAND.textPrimary}>
          Modelos de treino
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
          Modelos de treino
        </Title>
        <Alert color="red" icon={<IconAlertTriangle size={16} />} radius="md">
          {errorMessage(templatesQuery.error, 'Acesso restrito a administradores e personal trainers.')}
        </Alert>
      </Stack>
    );
  }

  const items = templatesQuery.data?.items || [];

  return (
    <Stack gap="xl">
      <Group justify="space-between" align="flex-end" wrap="wrap">
        <div>
          <Title order={1} c={BRAND.textPrimary}>
            Modelos de treino
          </Title>
          <Text c={BRAND.textMuted} size="md">
            Monte fichas-modelo reutilizáveis com slots cíclicos de grupo muscular/cardio e atribua a usuários. O
            motor de geração de ficha usa o modelo atribuído ao usuário antes de cair no comportamento padrão
            (ABC/Full Body).
          </Text>
        </div>
        <Group gap="sm" wrap="wrap">
          {canManage && (
            <Button
              variant="light"
              color="brand"
              leftSection={<IconSparkles size={16} />}
              onClick={openPeriodization}
            >
              Gerar modelo automaticamente
            </Button>
          )}
          <Button
            color="brand"
            leftSection={<IconPlus size={16} />}
            onClick={() => router.push('/modelos-treino/novo')}
          >
            Novo modelo
          </Button>
        </Group>
      </Group>

      {(currentUserQuery.isLoading || templatesQuery.isLoading) && <Text c={BRAND.textMuted}>Carregando...</Text>}
      {templatesQuery.isError && !forbidden && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />}>
          {errorMessage(templatesQuery.error, 'Não foi possível carregar os modelos de treino.')}
        </Alert>
      )}

      {templatesQuery.isSuccess && (
        <Table.ScrollContainer minWidth={760}>
          <Table striped highlightOnHover withTableBorder verticalSpacing="sm">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Nome</Table.Th>
                <Table.Th>Modo</Table.Th>
                <Table.Th>Atribuições</Table.Th>
                <Table.Th>Status</Table.Th>
                <Table.Th />
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {items.map((template) => (
                <Table.Tr key={template.id}>
                  <Table.Td>
                    <Group gap={6} wrap="nowrap">
                      <Text
                        fw={600}
                        c={BRAND.textPrimary}
                        style={{ cursor: 'pointer' }}
                        onClick={() => router.push(`/modelos-treino/${template.id}`)}
                      >
                        {template.name}
                      </Text>
                      {versionLabel(template.current_version?.number) && (
                        <Badge variant="outline" color="gray" size="sm" title="Versão vigente do modelo">
                          {versionLabel(template.current_version?.number)}
                        </Badge>
                      )}
                      <TrainingGoalBadge value={template.training_goal} onlyNonDefault />
                      {template.has_draft && (
                        <Badge variant="light" color="yellow" size="sm" title="Há um rascunho não publicado">
                          Rascunho
                        </Badge>
                      )}
                    </Group>
                    {template.description && (
                      <Text size="xs" c={BRAND.textMuted}>
                        {template.description}
                      </Text>
                    )}
                  </Table.Td>
                  <Table.Td>
                    <Badge variant="light" color={template.volume_mode === 'equipment_series' ? 'teal' : 'gray'}>
                      {volumeModeLabel(template.volume_mode)}
                    </Badge>
                  </Table.Td>
                  <Table.Td>
                    <Badge variant="light" color="brand">
                      {template.assignments_count}
                    </Badge>
                  </Table.Td>
                  <Table.Td>
                    <Switch
                      color="brand"
                      checked={template.is_active}
                      label={template.is_active ? 'Ativo' : 'Inativo'}
                      onChange={(event) =>
                        toggleStatusMutation.mutate({ id: template.id, isActive: event.currentTarget.checked })
                      }
                    />
                  </Table.Td>
                  <Table.Td>
                    <Group gap={4} justify="flex-end" wrap="nowrap">
                      <Button
                        size="xs"
                        variant="light"
                        color="brand"
                        onClick={() => router.push(`/modelos-treino/${template.id}`)}
                      >
                        Editar
                      </Button>
                      <Button
                        size="xs"
                        variant="light"
                        color="brand"
                        leftSection={<IconChartBar size={14} />}
                        onClick={() => router.push(`/diagnostico-volume/${template.id}`)}
                      >
                        Diagnóstico de volume
                      </Button>
                      <Button
                        size="xs"
                        variant="light"
                        color="brand"
                        leftSection={<IconUsers size={14} />}
                        onClick={() => openAssignModal(template)}
                      >
                        Atribuir
                      </Button>
                      <Button
                        size="xs"
                        variant="light"
                        color="brand"
                        leftSection={<IconCopy size={14} />}
                        loading={duplicateMutation.isPending && duplicateMutation.variables === template.id}
                        onClick={() => duplicateMutation.mutate(template.id)}
                      >
                        Duplicar
                      </Button>
                      <Button
                        size="xs"
                        variant="light"
                        color="red"
                        leftSection={<IconTrash size={14} />}
                        onClick={() => confirmDelete(template)}
                      >
                        Excluir
                      </Button>
                    </Group>
                  </Table.Td>
                </Table.Tr>
              ))}
              {items.length === 0 && (
                <Table.Tr>
                  <Table.Td colSpan={6}>
                    <Text c={BRAND.textMuted} ta="center" py="md">
                      Nenhum modelo de treino cadastrado ainda.
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
            Excluir o modelo &quot;{deleteTarget?.name}&quot;? Essa ação não pode ser desfeita e remove também suas
            atribuições ({deleteTarget?.assignments_count ?? 0}).
          </Text>
          {deleteMutation.isError && (
            <Alert color="red" icon={<IconAlertTriangle size={16} />}>
              {errorMessage(deleteMutation.error, 'Não foi possível excluir o modelo de treino.')}
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

      <PeriodizationModal opened={periodizationOpened} onClose={closePeriodization} />

      {assignTarget && (
        <AssignTemplateModal
          templateId={assignTarget.id}
          opened={assignOpened}
          onClose={() => {
            closeAssign();
            setAssignTarget(null);
          }}
          onAssigned={() => qc.invalidateQueries({ queryKey: ['workout-plan-templates'] })}
        />
      )}
    </Stack>
  );
}
