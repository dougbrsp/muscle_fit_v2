'use client';

import { Alert, Group, Loader, Paper, Stack, Table, Text, Title } from '@mantine/core';
import { IconAlertTriangle } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { TablePager } from '@/components/TablePager';
import { apiFetch, ApiError } from '@/lib/api';
import { usePagination } from '@/lib/pagination';
import { ApiListResponse, EffortEstimateEntry, formatDateBR } from '@/lib/physical-indicators-types';
import { BRAND } from '@/theme';

// M99: só leitura. EPOC e PAI de 7 dias chegam PRONTOS do backend (motor
// `cardio_load`); o lançamento manual saiu (a tabela antiga ficou como histórico).
export function EffortMetricsSection() {
  const listQuery = useQuery({
    queryKey: ['effort-metrics'],
    queryFn: () => apiFetch<ApiListResponse<EffortEstimateEntry>>('/effort-metrics'),
  });

  const items = listQuery.data?.items || [];
  const pagination = usePagination(items);

  return (
    <Stack gap="md">
      <div>
        <Title order={2} c={BRAND.textPrimary}>
          Esforço (EPOC / PAI)
        </Title>
        <Text c={BRAND.textMuted} size="sm">
          Acumulado de 7 dias estimado a partir do tempo em zona de FC das atividades do Intervals.icu. EPOC e PAI
          são métricas do relógio; aqui são estimativas, não a leitura do aparelho.
        </Text>
      </div>

      {listQuery.isLoading && (
        <Group>
          <Loader size="sm" color="brand" />
          <Text c={BRAND.textMuted}>Carregando dados de esforço...</Text>
        </Group>
      )}

      {listQuery.isError && (
        <Alert color="red" icon={<IconAlertTriangle />} title="Erro ao carregar dados">
          {listQuery.error instanceof ApiError ? listQuery.error.message : 'Não foi possível consultar a Fitness API.'}
        </Alert>
      )}

      {!listQuery.isLoading && !listQuery.isError && (
        <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
          <Table.ScrollContainer minWidth={420}>
            <Table striped highlightOnHover withTableBorder verticalSpacing="sm">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Data</Table.Th>
                  <Table.Th>EPOC 7 dias (estimado)</Table.Th>
                  <Table.Th>PAI 7 dias (estimado)</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {pagination.pageItems.map((entry) => (
                  <Table.Tr key={entry.local_date}>
                    <Table.Td style={{ whiteSpace: 'nowrap' }}>{formatDateBR(entry.local_date)}</Table.Td>
                    <Table.Td>{entry.epoc_7d_estimated ?? '-'}</Table.Td>
                    <Table.Td>{entry.pai_7d_estimated ?? '-'}</Table.Td>
                  </Table.Tr>
                ))}
                {items.length === 0 && (
                  <Table.Tr>
                    <Table.Td colSpan={3}>
                      <Text c={BRAND.textMuted} ta="center" py="md">
                        Sem atividades do Intervals.icu com tempo em zona de FC no período.
                      </Text>
                    </Table.Td>
                  </Table.Tr>
                )}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
          <TablePager
            page={pagination.page}
            totalPages={pagination.totalPages}
            onChange={pagination.setPage}
            visible={pagination.showPager}
          />
        </Paper>
      )}
    </Stack>
  );
}
