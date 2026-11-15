'use client';

import { ActionIcon, Alert, Badge, Button, Group, Loader, Select, Stack, Table, Text, Tooltip } from '@mantine/core';
import { IconAlertTriangle, IconTrash } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import {
  accessErrorMessage,
  accessKeys,
  addGroupMember,
  fetchGroupMembers,
  removeGroupMember,
} from '@/lib/access-admin-api';
import type { AccessGroup } from '@/lib/access-admin-types';
import { roleLabel, type UserAdminItem } from '@/lib/users-admin-types';
import { BRAND } from '@/theme';

type Props = {
  group: AccessGroup;
  users: UserAdminItem[];
  currentUserId: number | null;
  /** admin.grupos/editar: adicionar membro. */
  canEdit: boolean;
  /** admin.grupos/excluir: remover membro. */
  canDelete: boolean;
  /** admin.usuarios/ver: a lista de usuários (seletor) só existe com ela. */
  canListUsers: boolean;
};

export function MembrosGrupo({ group, users, currentUserId, canEdit, canDelete, canListUsers }: Props) {
  const qc = useQueryClient();
  const readOnly = !group.editable;
  const showAdd = canEdit && !readOnly;
  const showRemove = canDelete && !readOnly;
  const [selected, setSelected] = useState<string | null>(null);

  const membersQuery = useQuery({
    queryKey: accessKeys.members(group.id),
    queryFn: () => fetchGroupMembers(group.id),
    retry: false,
  });
  const members = membersQuery.data ?? [];

  async function refresh() {
    await Promise.all([
      qc.invalidateQueries({ queryKey: accessKeys.members(group.id) }),
      qc.invalidateQueries({ queryKey: accessKeys.groups }),
      qc.invalidateQueries({ queryKey: accessKeys.audit(group.id) }),
    ]);
  }

  const addMutation = useMutation({
    mutationFn: (userId: number) => addGroupMember(group.id, userId),
    onSuccess: async () => {
      setSelected(null);
      await refresh();
    },
  });
  const removeMutation = useMutation({
    mutationFn: (userId: number) => removeGroupMember(group.id, userId),
    onSuccess: refresh,
  });

  const memberIds = new Set(members.map((m) => m.user_id));
  // O admin não se vincula a si mesmo (o backend também recusa).
  const options = users
    .filter((u) => u.is_active && !memberIds.has(u.id) && u.id !== currentUserId)
    .map((u) => ({
      value: String(u.id),
      label: u.full_name ? `${u.full_name} (${u.username})` : u.username,
    }));

  return (
    <Stack gap="sm">
      {readOnly ? (
        <Alert color="gray" variant="light">
          Grupo de sistema: os membros são definidos pelo papel do usuário e não podem ser alterados aqui.
        </Alert>
      ) : showAdd && !canListUsers ? (
        <Alert color="yellow" variant="light" icon={<IconAlertTriangle size={16} />}>
          Seu acesso não inclui a lista de usuários: não é possível escolher novos membros aqui.
        </Alert>
      ) : showAdd ? (
        <Group align="flex-end" wrap="wrap">
          <Select
            label="Adicionar membro"
            placeholder="Escolha um usuário"
            searchable
            data={options}
            value={selected}
            onChange={setSelected}
            nothingFoundMessage="Nenhum usuário disponível"
            style={{ flex: 1, minWidth: 220 }}
          />
          <Button
            color="brand"
            disabled={!selected}
            loading={addMutation.isPending}
            onClick={() => {
              if (!selected) return;
              addMutation.reset();
              removeMutation.reset();
              addMutation.mutate(Number(selected));
            }}
          >
            Adicionar
          </Button>
        </Group>
      ) : null}

      {(addMutation.isError || removeMutation.isError) && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />}>
          {accessErrorMessage(addMutation.error ?? removeMutation.error, 'Não foi possível alterar os membros.')}
        </Alert>
      )}
      {addMutation.data?.already_member && <Text size="sm" c={BRAND.textMuted}>Esse usuário já era membro.</Text>}

      {membersQuery.isLoading && (
        <Group>
          <Loader size="sm" color="brand" />
          <Text c={BRAND.textMuted}>Carregando membros...</Text>
        </Group>
      )}
      {membersQuery.isError && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />}>
          {accessErrorMessage(membersQuery.error, 'Não foi possível carregar os membros.')}
        </Alert>
      )}

      {membersQuery.isSuccess && (
        <Table.ScrollContainer minWidth={420}>
          <Table withTableBorder striped verticalSpacing="xs">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Usuário</Table.Th>
                <Table.Th>Papel</Table.Th>
                <Table.Th>Status</Table.Th>
                {showRemove && <Table.Th />}
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {members.map((m) => (
                <Table.Tr key={m.user_id}>
                  <Table.Td>
                    <Text size="sm" fw={600}>{m.full_name || m.username}</Text>
                    {m.full_name && <Text size="xs" c={BRAND.textMuted}>{m.username}</Text>}
                  </Table.Td>
                  <Table.Td>{roleLabel(m.role)}</Table.Td>
                  <Table.Td>
                    <Badge variant="light" color={m.is_active ? 'teal' : 'red'}>
                      {m.is_active ? 'Ativo' : 'Desativado'}
                    </Badge>
                  </Table.Td>
                  {showRemove && (
                    <Table.Td>
                      <Tooltip label="Remover do grupo">
                        <ActionIcon
                          color="red"
                          variant="subtle"
                          aria-label={`Remover ${m.username} do grupo`}
                          loading={removeMutation.isPending && removeMutation.variables === m.user_id}
                          onClick={() => {
                            addMutation.reset();
                            removeMutation.reset();
                            removeMutation.mutate(m.user_id);
                          }}
                        >
                          <IconTrash size={16} />
                        </ActionIcon>
                      </Tooltip>
                    </Table.Td>
                  )}
                </Table.Tr>
              ))}
              {members.length === 0 && (
                <Table.Tr>
                  <Table.Td colSpan={4}>
                    <Text c={BRAND.textMuted} ta="center">
                      Nenhum membro.
                    </Text>
                  </Table.Td>
                </Table.Tr>
              )}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      )}
    </Stack>
  );
}
