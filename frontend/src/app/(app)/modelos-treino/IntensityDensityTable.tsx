'use client';

import { Alert, Loader, Stack, Table, Text } from '@mantine/core';
import { IconAlertTriangle } from '@tabler/icons-react';
import { ApiError } from '@/lib/api';
import { formatIndexPercent, type IntensityDensityGroupRow } from '@/lib/intensity-density';
import { BRAND } from '@/theme';
import { directionTextColor, IntensityDensityBar } from './IntensityDensityBar';

export type IntensityDensityTableProps = {
  rows: IntensityDensityGroupRow[];
  hasAnyFilledSlot: boolean;
  isPending: boolean;
  isError: boolean;
  error?: unknown;
};

/**
 * Tabela "Intensidade × Densidade por grupo" do builder — as MESMAS linhas da
 * janela flutuante. Estados de carregando/erro/vazio como em
 * `WeeklyActivationPreviewTable`; aparece também no mobile.
 */
export function IntensityDensityTable({ rows, hasAnyFilledSlot, isPending, isError, error }: IntensityDensityTableProps) {
  if (!hasAnyFilledSlot) {
    return (
      <Text c={BRAND.textMuted} size="sm">
        Preencha ao menos um slot para ver intensidade e densidade.
      </Text>
    );
  }

  if (isPending) {
    return (
      <Stack align="center" py="md">
        <Loader size="sm" color="brand" />
      </Stack>
    );
  }

  if (isError) {
    return (
      <Alert color="red" icon={<IconAlertTriangle size={16} />}>
        {error instanceof ApiError ? error.message : 'Erro ao calcular intensidade e densidade.'}
      </Alert>
    );
  }

  if (rows.length === 0) {
    return (
      <Text c={BRAND.textMuted} size="sm">
        Nenhum grupo principal ou secundário na janela de 7 dias.
      </Text>
    );
  }

  return (
    <Table.ScrollContainer minWidth={560}>
      <Table striped highlightOnHover withTableBorder>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Grupo</Table.Th>
            <Table.Th style={{ minWidth: 180 }}>Barra</Table.Th>
            <Table.Th ta="right">Intensidade</Table.Th>
            <Table.Th ta="right">Densidade/Volume</Table.Th>
            <Table.Th>Direção</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {rows.map((row) => (
            <Table.Tr key={row.muscleGroup}>
              <Table.Td>
                <Text size="sm" c={BRAND.textPrimary}>
                  {row.muscleGroup}
                </Text>
              </Table.Td>
              <Table.Td>
                <IntensityDensityBar row={row} withLegend />
              </Table.Td>
              <Table.Td ta="right">
                <Text size="sm" c={BRAND.textPrimary}>
                  {formatIndexPercent(row.intensity)}
                </Text>
              </Table.Td>
              <Table.Td ta="right">
                <Text size="sm" c={BRAND.textPrimary}>
                  {formatIndexPercent(row.density)}
                </Text>
              </Table.Td>
              <Table.Td>
                <Text size="sm" fw={600} c={directionTextColor(row)}>
                  {row.label}
                </Text>
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}
