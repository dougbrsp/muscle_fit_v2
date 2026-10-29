'use client';

import { Alert, Group, Paper, Stack, Table, Text, Title } from '@mantine/core';
import { IconInfoCircle } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import Link from 'next/link';
import { MuscleStatusBadge } from '@/components/dashboard/MuscleStatusBadge';
import { StatusBadge } from '@/components/dashboard/StatusBadge';
import { DashboardCardSkeleton, EmptyState, ErrorState } from '@/components/dashboard/states';
import { dashboardKeys, fetchCycle } from '@/lib/dashboard-api';
import { fmtSets } from '@/lib/dashboard-format';
import type { CycleSlot } from '@/lib/dashboard-v2-types';
import { BRAND } from '@/theme';

// 1 = segunda ... 6 = sábado, mesma convenção do builder de Modelos de treino.
const WEEKDAY_LABELS: Record<number, string> = {
  1: 'Segunda',
  2: 'Terça',
  3: 'Quarta',
  4: 'Quinta',
  5: 'Sexta',
  6: 'Sábado',
  7: 'Domingo',
};

function SlotCard({ slot }: { slot: CycleSlot }) {
  return (
    <Paper
      withBorder
      radius="md"
      p="sm"
      style={{
        background: BRAND.bgSurface,
        borderColor: slot.status === 'next' ? BRAND.orange : BRAND.border,
        minWidth: 170,
        flex: '1 1 170px',
      }}
    >
      <Group justify="space-between" mb={4} wrap="nowrap">
        <Text size="xs" c={BRAND.textMuted}>
          #{slot.sequence}
        </Text>
        <StatusBadge domain="cycleSlot" code={slot.status} />
      </Group>
      <Text fw={700} c={BRAND.textPrimary}>
        {slot.name}
      </Text>
      <Text size="xs" c={BRAND.textMuted}>
        {slot.slot_type}
        {slot.weekday != null ? ` · ${WEEKDAY_LABELS[slot.weekday] ?? `Dia ${slot.weekday}`}` : ''}
      </Text>
    </Paper>
  );
}

/**
 * `/dashboard/ciclo` — posição no ciclo do Modelo de Ficha atribuído ao
 * usuário e cobertura de volume por grupo muscular até o fim do ciclo.
 *
 * Endpoint: `GET /analytics/dashboard-cycle` (`api/app/analytics.py`). Sem
 * parâmetro de período: a janela (`cycle_length_days`) é derivada do número de
 * slots do próprio Modelo. Nada aqui é hardcodado por letra A/B/C — os slots
 * vêm nomeados do Modelo cadastrado, então o ciclo ABC é apenas um caso
 * particular.
 *
 * Tudo em séries equivalentes por SEMANA, a semana do backend (M76 Lote 4a):
 * "Realizado" = últimos 7 dias; "Planejado / semana" = o planejado do Modelo
 * nos 7 dias a partir do 1º slot de treino (o mesmo número do editor de
 * Modelo). O Status vem pronto da API e classifica o planejado da semana
 * contra a meta — "se eu cumprir o Modelo, onde fico na semana?". Nenhuma
 * conta aqui: a tela só mostra.
 */
export function DashboardCicloClient() {
  const cycleQuery = useQuery({
    queryKey: dashboardKeys.cycle(),
    queryFn: fetchCycle,
  });

  const data = cycleQuery.data;

  return (
    <Stack gap="lg">
      <div>
        <Title order={2} c={BRAND.textPrimary}>
          Ciclo de treino
        </Title>
        <Text size="sm" c={BRAND.textMuted}>
          Posição no ciclo do modelo de ficha atribuído a você e cobertura de volume por grupo muscular.
        </Text>
      </div>

      {cycleQuery.isLoading && <DashboardCardSkeleton rows={4} />}
      {cycleQuery.isError && (
        <ErrorState title="Não foi possível carregar o ciclo de treino." error={cycleQuery.error} />
      )}

      {data && !data.has_template && (
        <Alert color="gray" variant="light" icon={<IconInfoCircle size={18} />}>
          {data.message}{' '}
          <Text component={Link} href="/planos-treino" fw={600} c={BRAND.orange} span>
            Ver planos de treino
          </Text>
        </Alert>
      )}

      {data?.has_template && (
        <>
          <Paper withBorder radius="lg" p="md" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
            <Group justify="space-between" mb="sm" wrap="wrap">
              <Title order={4} c={BRAND.textPrimary}>
                {data.template_name}
              </Title>
              <Text size="sm" c={BRAND.textMuted}>
                Ciclo de {data.cycle_length_days} dias · {data.slots.length} slot(s)
                {data.next_slot ? ` · próximo: ${data.next_slot.name}` : ''}
              </Text>
            </Group>
            <Group gap="sm" align="stretch" wrap="wrap">
              {data.slots.map((slot) => (
                <SlotCard key={slot.sequence} slot={slot} />
              ))}
            </Group>
          </Paper>

          {data.training_type_source === 'unavailable' ? (
            <Alert color="blue" variant="light" icon={<IconInfoCircle size={18} />}>
              Nenhuma Meta de Ativação resolvida para este modelo — a cobertura do ciclo por grupo muscular fica
              indisponível até que uma Meta de Ativação seja vinculada ao modelo ou atribuído a você.
            </Alert>
          ) : (
            <Paper withBorder radius="lg" p="md" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
              <Group justify="space-between" mb="xs" wrap="wrap">
                <Title order={4} c={BRAND.textPrimary}>
                  Cobertura do ciclo
                </Title>
                <Text size="xs" c={BRAND.textMuted}>
                  Realizado = últimos 7 dias. Planejado = semana do Modelo (7 dias a partir do 1º treino). Status =
                  planejado × meta.
                </Text>
              </Group>

              {data.coverage.length === 0 ? (
                <EmptyState
                  title="Nenhum grupo muscular no ciclo"
                  description="Nenhum grupo com meta, realizado ou projetado neste ciclo."
                />
              ) : (
                <Table.ScrollContainer minWidth={680}>
                  <Table striped highlightOnHover withTableBorder verticalSpacing="xs">
                    <Table.Thead>
                      <Table.Tr>
                        <Table.Th>Grupo</Table.Th>
                        <Table.Th>Meta / semana</Table.Th>
                        <Table.Th>Realizado (7 dias)</Table.Th>
                        <Table.Th>Planejado / semana</Table.Th>
                        <Table.Th>Status</Table.Th>
                      </Table.Tr>
                    </Table.Thead>
                    <Table.Tbody>
                      {data.coverage.map((row) => (
                        <Table.Tr key={row.muscle_group_id}>
                          <Table.Td fw={600}>{row.muscle_group}</Table.Td>
                          <Table.Td>{fmtSets(row.target_weekly_sets)}</Table.Td>
                          <Table.Td>{fmtSets(row.actual_weekly_sets)}</Table.Td>
                          <Table.Td>{fmtSets(row.projected_weekly_sets)}</Table.Td>
                          <Table.Td>{row.status ? <MuscleStatusBadge status={row.status} /> : '-'}</Table.Td>
                        </Table.Tr>
                      ))}
                    </Table.Tbody>
                  </Table>
                </Table.ScrollContainer>
              )}
            </Paper>
          )}
        </>
      )}
    </Stack>
  );
}
