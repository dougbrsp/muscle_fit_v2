'use client';

import { Alert, Badge, Card, Stack, Table, Text, Title } from '@mantine/core';
import { IconAlertTriangle } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import Link from 'next/link';
import { nutritionErrorInfo } from '@/lib/nutrition-api';
import { checkinKeys, fetchCheckinHistory, formatAdjustment, verdictTone, weekRangeLabel, type Checkin } from '@/lib/nutrition-checkin';
import { BRAND } from '@/theme';

/** Tabela do histórico (apresentação pura do que `GET /nutrition/checkins` devolve). */
export function CheckinHistoryTable({ items }: { items: readonly Checkin[] }) {
  if (items.length === 0) {
    return (
      <Text size="sm" c={BRAND.textMuted}>
        Nenhum check-in ainda: o primeiro aparece depois da primeira semana fechada com o plano publicado.
      </Text>
    );
  }
  return (
    <Table.ScrollContainer minWidth={420}>
      <Table verticalSpacing={6} aria-label="Histórico de check-ins semanais">
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Semana</Table.Th>
            <Table.Th>Veredito</Table.Th>
            <Table.Th>Ajuste</Table.Th>
            <Table.Th>Situação</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {items.map((item) => (
            <Table.Tr key={item.id}>
              <Table.Td>{weekRangeLabel(item)}</Table.Td>
              <Table.Td>
                <Badge color={verdictTone(item.verdict)} variant="light">
                  {item.verdict_label}
                </Badge>
              </Table.Td>
              <Table.Td>{formatAdjustment(item.adjustment_kcal) ?? 'sem mudança'}</Table.Td>
              <Table.Td>{item.status_label}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}

/** Histórico dos check-ins no `/dashboard/nutricao`. */
export function CheckinHistory() {
  const consulta = useQuery({ queryKey: checkinKeys.history(), queryFn: () => fetchCheckinHistory(), retry: false });
  return (
    <Card withBorder radius="md" bg={BRAND.bgSurface}>
      <Stack gap="sm">
        <Stack gap={2}>
          <Title order={3} c={BRAND.textPrimary}>
            Check-ins semanais
          </Title>
          <Text size="sm" c={BRAND.textMuted}>
            A avaliação de cada semana fechada e o que mudou nas metas.{' '}
            <Link href="/nutricao/checkin" style={{ color: BRAND.orange }}>
              Abrir o check-in
            </Link>
          </Text>
        </Stack>
        {consulta.isLoading && <Text size="sm" c={BRAND.textMuted}>Carregando...</Text>}
        {consulta.isError && (
          <Alert color="red" icon={<IconAlertTriangle size={16} />}>
            {nutritionErrorInfo(consulta.error, 'Não foi possível carregar o histórico de check-ins.').message}
          </Alert>
        )}
        {consulta.data && <CheckinHistoryTable items={consulta.data.items} />}
      </Stack>
    </Card>
  );
}
