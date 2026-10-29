'use client';

import { Alert, Badge, Loader, Paper, Stack, Table, Text, Title, Tooltip } from '@mantine/core';
import { formatDateTimeOrDash } from '@/lib/format';
import type { StoredRuleSet } from '@/lib/next-set-load-rules-types';
import { BRAND } from '@/theme';

function statusBadge(item: StoredRuleSet) {
  if (item.status === 'draft') return <Badge color="gray" variant="light">Rascunho</Badge>;
  if (item.current) return <Badge color="green" variant="light">Vigente</Badge>;
  return <Badge color="blue" variant="outline">Encerrada</Badge>;
}

/** Histórico (`GET /next-set-load-rules/versions`): rascunho no topo, depois as publicadas da mais nova à mais antiga. */
export function VersionsList({
  versions,
  isLoading,
  error,
  userName,
}: {
  versions: StoredRuleSet[];
  isLoading: boolean;
  error: string | null;
  userName: (id: number | null) => string;
}) {
  return (
    <Paper withBorder radius="md" p="md">
      <Stack gap="sm">
        <Title order={4}>Histórico de versões</Title>
        {isLoading && <Loader size="sm" color="brand" />}
        {error && <Alert color="red">{error}</Alert>}
        {!isLoading && !error && versions.length === 0 && (
          <Text size="sm" c={BRAND.textMuted}>
            Nenhuma versão publicada: vale o padrão do sistema.
          </Text>
        )}
        {versions.length > 0 && (
          <Table.ScrollContainer minWidth={720}>
            <Table striped withTableBorder verticalSpacing="xs">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Versão</Table.Th>
                  <Table.Th>Situação</Table.Th>
                  <Table.Th>Publicada em</Table.Th>
                  <Table.Th>Por</Table.Th>
                  <Table.Th>Regras</Table.Th>
                  <Table.Th>Notas</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {versions.map((item) => (
                  <Table.Tr key={item.id}>
                    <Table.Td>{item.version != null ? `v${item.version}` : '—'}</Table.Td>
                    <Table.Td>{statusBadge(item)}</Table.Td>
                    <Table.Td>
                      {item.status === 'draft'
                        ? `salvo em ${formatDateTimeOrDash(item.updated_at)}`
                        : formatDateTimeOrDash(item.published_at)}
                    </Table.Td>
                    <Table.Td>{userName(item.status === 'draft' ? item.created_by : item.published_by)}</Table.Td>
                    <Table.Td>
                      <Tooltip label={<span style={{ whiteSpace: 'pre-line' }}>{item.summary}</span>} multiline w={420} withArrow disabled={!item.summary}>
                        <Text size="sm" style={{ cursor: item.summary ? 'help' : undefined }} data-summary>
                          {item.ruleset.rules.length}
                        </Text>
                      </Tooltip>
                    </Table.Td>
                    <Table.Td>
                      <Text size="sm" c={item.notes ? undefined : BRAND.textMuted}>
                        {item.notes || '—'}
                      </Text>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        )}
      </Stack>
    </Paper>
  );
}
