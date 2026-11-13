'use client';

import { Alert, Group, Loader, Table, Text } from '@mantine/core';
import { IconAlertTriangle } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { accessErrorMessage, accessKeys, fetchGroupAudit } from '@/lib/access-admin-api';
import type { AccessGroup } from '@/lib/access-admin-types';
import { auditActionLabel } from '@/lib/access-grants';
import type { UserAdminItem } from '@/lib/users-admin-types';
import { BRAND } from '@/theme';

type Props = { group: AccessGroup; users: UserAdminItem[] };

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('pt-BR');
}

export function AuditoriaGrupo({ group, users }: Props) {
  const query = useQuery({
    queryKey: accessKeys.audit(group.id),
    queryFn: () => fetchGroupAudit(group.id),
    retry: false,
  });
  const names = new Map(users.map((u) => [u.id, u.full_name || u.username]));

  if (query.isLoading) {
    return (
      <Group>
        <Loader size="sm" color="brand" />
        <Text c={BRAND.textMuted}>Carregando auditoria...</Text>
      </Group>
    );
  }
  if (query.isError) {
    return (
      <Alert color="red" icon={<IconAlertTriangle size={16} />}>
        {accessErrorMessage(query.error, 'Não foi possível carregar a auditoria.')}
      </Alert>
    );
  }
  const entries = query.data ?? [];
  return (
    <Table.ScrollContainer minWidth={420}>
      <Table withTableBorder striped verticalSpacing="xs">
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Data</Table.Th>
            <Table.Th>Ação</Table.Th>
            <Table.Th>Autor</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {entries.map((e) => (
            <Table.Tr key={e.id}>
              <Table.Td>{formatDate(e.created_at)}</Table.Td>
              <Table.Td>{auditActionLabel(e.action)}</Table.Td>
              <Table.Td>
                {e.actor_user_id == null ? '—' : names.get(e.actor_user_id) ?? `Usuário #${e.actor_user_id}`}
              </Table.Td>
            </Table.Tr>
          ))}
          {entries.length === 0 && (
            <Table.Tr>
              <Table.Td colSpan={3}>
                <Text c={BRAND.textMuted} ta="center">
                  Nenhum registro de auditoria.
                </Text>
              </Table.Td>
            </Table.Tr>
          )}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}
