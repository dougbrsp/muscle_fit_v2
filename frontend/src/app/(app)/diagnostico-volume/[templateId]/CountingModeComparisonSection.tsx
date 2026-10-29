'use client';

import { Alert, Badge, Group, Loader, Stack, Table, Text, Title, Tooltip } from '@mantine/core';
import { IconAlertTriangle, IconArrowsExchange } from '@tabler/icons-react';
import { useMemo } from 'react';
import {
  buildCountingModeHeaders,
  buildCountingModeRows,
  uncalibratedWarning,
} from '@/lib/counting-mode-comparison';
import type { CountingModeComparison } from '@/lib/methodology-volume-types';
import { BRAND } from '@/theme';
import { UNCALIBRATED_BADGE_LABEL, UNCALIBRATED_HINT } from '../../modelos-treino/MethodologyVolumeTable';

export type CountingModeComparisonSectionProps = {
  comparison: CountingModeComparison | null | undefined;
  isPending: boolean;
  error: string | null;
};

/**
 * Diagnóstico de volume — o MESMO Modelo avaliado em cada modo de contagem,
 * lado a lado (Lote 3): séries por semana, bucket de frequência e status por
 * grupo, com as transições destacadas. Tudo calculado pelo backend
 * (`GET .../methodology-volume/counting-modes`); aqui só desenha. Serve para o
 * usuário ver o efeito ANTES de trocar o modo no editor do Modelo.
 */
export function CountingModeComparisonSection({ comparison, isPending, error }: CountingModeComparisonSectionProps) {
  const headers = useMemo(() => (comparison ? buildCountingModeHeaders(comparison) : []), [comparison]);
  const rows = useMemo(() => (comparison ? buildCountingModeRows(comparison) : []), [comparison]);
  const warning = comparison ? uncalibratedWarning(comparison) : null;
  const transitions = rows.filter((row) => row.statusChanged || row.bucketChanged).length;

  return (
    <Stack gap="xs" data-counting-mode-comparison>
      <Group justify="space-between" align="flex-end" wrap="wrap">
        <Title order={3} c={BRAND.textPrimary}>
          Contagem do volume: modos lado a lado
        </Title>
        {comparison && (
          <Text size="xs" c={BRAND.textMuted}>
            {transitions === 0
              ? 'Nenhum grupo muda de status ou de frequência entre os modos.'
              : `${transitions} ${transitions === 1 ? 'grupo muda' : 'grupos mudam'} de status ou de frequência entre os modos.`}
          </Text>
        )}
      </Group>
      <Text size="sm" c={BRAND.textMuted}>
        O Modelo salvo avaliado em cada modo, sem gravar nada. O modo do Modelo se troca no editor.
      </Text>
      {headers.length > 0 && (
        <Stack gap={2}>
          {headers.map((header) => (
            <Text key={header.mode} size="xs" c={BRAND.textMuted}>
              <b>{header.label}</b>
              {header.isCurrent ? ' (modo do Modelo)' : ''}: {header.description}
            </Text>
          ))}
        </Stack>
      )}
      {warning && (
        <Alert color="yellow" variant="light" icon={<IconAlertTriangle size={16} />} data-uncalibrated-warning>
          {warning}
        </Alert>
      )}
      {isPending && !comparison ? (
        <Group justify="center" py="xs">
          <Loader size="xs" color="brand" />
        </Group>
      ) : error && !comparison ? (
        <Text size="xs" c={BRAND.textMuted}>
          <IconAlertTriangle size={12} style={{ verticalAlign: 'middle' }} /> Não foi possível comparar os modos ({error}).
        </Text>
      ) : rows.length === 0 ? (
        <Text size="xs" c={BRAND.textMuted}>
          Nenhum grupo com trabalho contado em nenhum dos modos.
        </Text>
      ) : (
        <Table.ScrollContainer minWidth={720}>
          <Table striped withTableBorder>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Grupo / bloco</Table.Th>
                {headers.map((header) => (
                  <Table.Th key={header.mode}>
                    {header.label}
                    {header.isCurrent ? ' (modo do Modelo)' : ''} — séries/semana · frequência · status
                  </Table.Th>
                ))}
                <Table.Th>Transição</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {rows.map((row) => (
                <Table.Tr key={row.key} data-comparison-row={row.key} data-transition={row.statusChanged || row.bucketChanged}>
                  <Table.Td>
                    <Text size="sm" fw={600} c={BRAND.textPrimary}>
                      {row.groupName}
                    </Text>
                    <Text size="xs" c={BRAND.textMuted}>
                      {row.className}
                    </Text>
                  </Table.Td>
                  {row.cells.map((cell) => {
                    const StatusIcon = cell.status.Icon;
                    return (
                      <Table.Td key={cell.mode}>
                        <Stack gap={2}>
                          <Text size="sm" c={BRAND.textPrimary}>
                            {cell.weeklyText}
                            {cell.weeklyRangeText && (
                              <Text component="span" size="xs" c={BRAND.textMuted}>
                                {' · '}
                                {cell.weeklyRangeText}
                              </Text>
                            )}
                          </Text>
                          <Text size="xs" c={BRAND.textMuted}>
                            {cell.bucketText}
                          </Text>
                          <Group gap={4} wrap="nowrap">
                            <StatusIcon
                              size={14}
                              role="img"
                              aria-label={`${row.groupName} em ${cell.modeLabel}: ${cell.statusLabel}`}
                              style={{ color: `var(--mantine-color-${cell.status.color}-6)`, flexShrink: 0 }}
                            />
                            <Text size="xs" c={BRAND.textPrimary}>
                              {cell.statusLabel}
                            </Text>
                          </Group>
                          {cell.uncalibrated && (
                            <Tooltip label={UNCALIBRATED_HINT} multiline w={300} withArrow>
                              <Badge size="xs" variant="outline" color="yellow" w="fit-content">
                                {UNCALIBRATED_BADGE_LABEL}
                              </Badge>
                            </Tooltip>
                          )}
                        </Stack>
                      </Table.Td>
                    );
                  })}
                  <Table.Td>
                    {row.statusChanged || row.bucketChanged ? (
                      <Stack gap={2}>
                        <Badge
                          size="sm"
                          variant="light"
                          color="orange"
                          leftSection={<IconArrowsExchange size={12} aria-hidden />}
                          w="fit-content"
                        >
                          {row.statusChanged ? 'muda o status' : 'muda a frequência'}
                        </Badge>
                        {row.transitionText && (
                          <Text size="xs" c={BRAND.textMuted}>
                            {row.transitionText}
                          </Text>
                        )}
                        {row.statusChanged && row.bucketChanged && (
                          <Text size="xs" c={BRAND.textMuted}>
                            e a frequência
                          </Text>
                        )}
                      </Stack>
                    ) : (
                      <Text size="xs" c={BRAND.textMuted}>
                        igual
                      </Text>
                    )}
                  </Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      )}
    </Stack>
  );
}
