'use client';

import { Alert, Badge, Button, Card, Group, Loader, Modal, Select, SegmentedControl, Stack, Table, Text } from '@mantine/core';
import { IconAlertTriangle, IconLinkOff, IconPlus } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import {
  accessErrorMessage,
  accessKeys,
  createTrainerLink,
  endTrainerLink,
  fetchGroupMembers,
  fetchTrainerLinks,
} from '@/lib/access-admin-api';
import type { AccessGroup, TrainerLink } from '@/lib/access-admin-types';
import {
  canEndLink,
  isLinkStatusFilter,
  LINK_STATUS_FILTER_OPTIONS,
  linkStatusColor,
  linkStatusLabel,
  linkStatusParam,
  PERSONAL_TRAINER_GROUP_KEY,
  personLabel,
  studentOptions,
  trainerOptions,
  type LinkStatusFilter,
} from '@/lib/access-links';
import type { UserAdminItem } from '@/lib/users-admin-types';
import { BRAND } from '@/theme';

type Props = {
  groups: AccessGroup[];
  users: UserAdminItem[];
  currentUserId: number | null;
  /** admin.grupos/editar: criar vínculo. */
  canEdit: boolean;
  /** admin.grupos/excluir: encerrar vínculo. */
  canDelete: boolean;
  /** admin.usuarios/ver: a lista de usuários (escolha do aluno) só existe com ela. */
  canListUsers: boolean;
};

function formatDate(value: string | null): string {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString('pt-BR');
}

/**
 * Aba "Vínculos": quem é treinador de quem (lote 5B, ADR 0011). Só transporte e
 * apresentação; as regras (treinador no grupo personal_trainer, ator fora do par, par já
 * ativo) são do backend, que devolve a mensagem exibida nos erros.
 */
export function VinculosTreinador({ groups, users, currentUserId, canEdit, canDelete, canListUsers }: Props) {
  const qc = useQueryClient();
  const [filter, setFilter] = useState<LinkStatusFilter>('ativo');
  const [createOpen, setCreateOpen] = useState(false);
  const [trainerId, setTrainerId] = useState<string | null>(null);
  const [studentId, setStudentId] = useState<string | null>(null);
  const [toEnd, setToEnd] = useState<TrainerLink | null>(null);

  const trainerGroup = groups.find((g) => g.group_key === PERSONAL_TRAINER_GROUP_KEY) ?? null;

  const linksQuery = useQuery({
    queryKey: accessKeys.links(linkStatusParam(filter)),
    queryFn: () => fetchTrainerLinks({ status: linkStatusParam(filter) }),
    retry: false,
    refetchOnWindowFocus: false,
  });
  // Para excluir candidatos que já têm vínculo ativo com o treinador escolhido (independe do filtro da lista).
  const activeLinksQuery = useQuery({
    queryKey: accessKeys.links('ativo'),
    queryFn: () => fetchTrainerLinks({ status: 'ativo' }),
    enabled: createOpen,
    retry: false,
  });
  const trainersQuery = useQuery({
    queryKey: trainerGroup ? accessKeys.members(trainerGroup.id) : ['access-admin', 'members', 'sem-grupo'],
    queryFn: () => fetchGroupMembers((trainerGroup as AccessGroup).id),
    enabled: createOpen && trainerGroup !== null,
    retry: false,
  });

  async function refresh() {
    await qc.invalidateQueries({ queryKey: accessKeys.linksAll });
  }

  const createMutation = useMutation({
    mutationFn: () => createTrainerLink({ trainer_user_id: Number(trainerId), student_user_id: Number(studentId) }),
    onSuccess: async () => {
      setCreateOpen(false);
      setTrainerId(null);
      setStudentId(null);
      await refresh();
    },
  });
  const endMutation = useMutation({
    mutationFn: (id: number) => endTrainerLink(id),
    onSuccess: async () => {
      setToEnd(null);
      await refresh();
    },
  });

  const links = linksQuery.data ?? [];
  const trainerOpts = trainerOptions(trainersQuery.data ?? [], currentUserId);
  const studentOpts = studentOptions(users, activeLinksQuery.data ?? [], currentUserId, trainerId ? Number(trainerId) : null);
  const canSubmit = trainerId !== null && studentId !== null;

  function openCreate() {
    createMutation.reset();
    setTrainerId(null);
    setStudentId(null);
    setCreateOpen(true);
  }

  return (
    <Stack gap="md">
      <Group justify="space-between" align="flex-end" wrap="wrap">
        <SegmentedControl
          aria-label="Filtro de status dos vínculos"
          value={filter}
          onChange={(value) => isLinkStatusFilter(value) && setFilter(value)}
          data={LINK_STATUS_FILTER_OPTIONS}
        />
        {canEdit && (
          <Button color="brand" leftSection={<IconPlus size={16} />} onClick={openCreate}>
            Novo vínculo
          </Button>
        )}
      </Group>

      {linksQuery.isLoading && (
        <Group>
          <Loader size="sm" color="brand" />
          <Text c={BRAND.textMuted}>Carregando vínculos...</Text>
        </Group>
      )}
      {linksQuery.isError && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />}>
          {accessErrorMessage(linksQuery.error, 'Não foi possível carregar os vínculos.')}
        </Alert>
      )}
      {endMutation.isError && !toEnd && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />}>
          {accessErrorMessage(endMutation.error, 'Não foi possível encerrar o vínculo.')}
        </Alert>
      )}

      {linksQuery.isSuccess && links.length === 0 && (
        <Text c={BRAND.textMuted}>Nenhum vínculo {filter === 'todos' ? 'cadastrado' : filter === 'ativo' ? 'ativo' : 'encerrado'}.</Text>
      )}

      {linksQuery.isSuccess && links.length > 0 && (
        <Card withBorder padding={0} style={{ overflowX: 'auto' }}>
          <Table striped verticalSpacing="xs">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Treinador</Table.Th>
                <Table.Th>Aluno</Table.Th>
                <Table.Th>Status</Table.Th>
                <Table.Th>Criado em</Table.Th>
                <Table.Th>Encerrado em</Table.Th>
                {canDelete && <Table.Th aria-label="Ações" />}
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {links.map((link) => (
                <Table.Tr key={link.id}>
                  <Table.Td>{personLabel(link.trainer_username, link.trainer_full_name)}</Table.Td>
                  <Table.Td>{personLabel(link.student_username, link.student_full_name)}</Table.Td>
                  <Table.Td>
                    <Badge color={linkStatusColor(link.status)} variant="light">
                      {linkStatusLabel(link.status)}
                    </Badge>
                  </Table.Td>
                  <Table.Td>{formatDate(link.created_at)}</Table.Td>
                  <Table.Td>{formatDate(link.ended_at)}</Table.Td>
                  {canDelete && (
                    <Table.Td>
                      {canEndLink(link, currentUserId) && (
                        <Button
                          size="xs"
                          variant="light"
                          color="red"
                          leftSection={<IconLinkOff size={14} />}
                          onClick={() => {
                            endMutation.reset();
                            setToEnd(link);
                          }}
                        >
                          Encerrar
                        </Button>
                      )}
                    </Table.Td>
                  )}
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Card>
      )}

      <Modal opened={toEnd !== null} onClose={() => setToEnd(null)} title="Encerrar vínculo?" size="sm">
        {toEnd && (
          <Stack gap="md">
            <Text>
              Encerrar o vínculo de {personLabel(toEnd.trainer_username, toEnd.trainer_full_name)} com o aluno{' '}
              {personLabel(toEnd.student_username, toEnd.student_full_name)}? O treinador perde o acesso aos dados do
              aluno; o histórico fica registrado.
            </Text>
            {endMutation.isError && (
              <Alert color="red" icon={<IconAlertTriangle size={16} />}>
                {accessErrorMessage(endMutation.error, 'Não foi possível encerrar o vínculo.')}
              </Alert>
            )}
            <Group justify="flex-end">
              <Button variant="default" onClick={() => setToEnd(null)}>
                Cancelar
              </Button>
              <Button color="red" loading={endMutation.isPending} onClick={() => endMutation.mutate(toEnd.id)}>
                Encerrar vínculo
              </Button>
            </Group>
          </Stack>
        )}
      </Modal>

      <Modal opened={createOpen} onClose={() => setCreateOpen(false)} title="Novo vínculo treinador–aluno">
        <Stack gap="md">
          {!canListUsers && (
            <Alert color="yellow" variant="light" icon={<IconAlertTriangle size={16} />}>
              Seu acesso não inclui a lista de usuários: não é possível escolher o aluno.
            </Alert>
          )}
          {trainerGroup === null && (
            <Alert color="yellow" variant="light" icon={<IconAlertTriangle size={16} />}>
              O grupo de sistema personal_trainer não foi encontrado.
            </Alert>
          )}
          {trainersQuery.isError && (
            <Alert color="red" icon={<IconAlertTriangle size={16} />}>
              {accessErrorMessage(trainersQuery.error, 'Não foi possível carregar os treinadores.')}
            </Alert>
          )}
          <Select
            label="Treinador"
            description="Membros do grupo personal_trainer."
            placeholder={trainersQuery.isLoading ? 'Carregando...' : 'Escolha o treinador'}
            data={trainerOpts}
            value={trainerId}
            onChange={(value) => {
              setTrainerId(value);
              setStudentId(null);
            }}
            searchable
            nothingFoundMessage="Nenhum treinador disponível."
            disabled={trainerGroup === null}
          />
          <Select
            label="Aluno"
            placeholder="Escolha o aluno"
            data={studentOpts}
            value={studentId}
            onChange={setStudentId}
            searchable
            nothingFoundMessage="Nenhum aluno disponível."
            disabled={!canListUsers || trainerId === null}
          />
          {createMutation.isError && (
            <Alert color="red" icon={<IconAlertTriangle size={16} />}>
              {accessErrorMessage(createMutation.error, 'Não foi possível criar o vínculo.')}
            </Alert>
          )}
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setCreateOpen(false)}>
              Cancelar
            </Button>
            <Button
              color="brand"
              disabled={!canSubmit || !canListUsers}
              loading={createMutation.isPending}
              onClick={() => createMutation.mutate()}
            >
              Criar vínculo
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
