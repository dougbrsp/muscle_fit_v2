'use client';

import { Alert, Box, Group, Paper, Table, Text, ThemeIcon, Title, Tooltip } from '@mantine/core';
import { IconAlertTriangle, IconFlame } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { useMemo } from 'react';
import { DashboardSkeleton, EmptyState, ErrorState } from '@/components/dashboard/states';
import { apiFetch } from '@/lib/api';
import type { MethodologyRealizedResponse, MuscleRecoveryConsecutiveCheckResponse } from '@/lib/analytics-types';
import { statusVisual } from '@/lib/dashboard-status';
import {
  ACTIVATION_WINDOW_DAYS,
  WEEKLY_STATUS_LABEL,
  buildLast7Days,
  consecutivePairsByBlock,
  dayTooltip,
  formatDayMonth,
  insufficientPairByDay,
  perSessionLabel,
  recoveryLabel,
  sessionTooltip,
  toActivationRows,
  weeklyTooltip,
  type ActivationRow,
  type ConsecutivePair,
  type RangeStatus,
} from '@/lib/muscle-activation-7d';
import { formatDecimal } from '@/lib/volume-classes';
import { BRAND } from '@/theme';

/**
 * "Ativação muscular (últimos 7 dias)" — veio de `/treinos`. Cada célula é o
 * nº de séries DIRETAS do grupo no dia, pintada contra a faixa por treino do
 * macro-grupo (Fase 6) na frequência real da janela; a última coluna soma a
 * semana contra a meta semanal. Os status vêm prontos de
 * `GET /analytics/methodology-realized` (M76 E6); aqui só se desenha.
 *
 * Janela fixa de 7 dias corridos, independente do período do topo da página
 * (mesmo recorte da tabela antiga).
 */

export const ACTIVATION_QUERY_KEY = ['methodology-realized', ACTIVATION_WINDOW_DAYS] as const;
export const RECOVERY_CHECK_QUERY_KEY = ['muscle-recovery-consecutive-check', ACTIVATION_WINDOW_DAYS] as const;

// Paleta do volume semanal do dashboard (fonte única em `dashboard-status`).
const STATUS_HEX: Record<Exclude<RangeStatus, 'NO_TARGET'>, string> = {
  WITHIN: statusVisual('weeklyVolume', 'ON_TARGET').hex,
  BELOW: statusVisual('weeklyVolume', 'BELOW_MIN').hex,
  ABOVE: statusVisual('weeklyVolume', 'ABOVE_MAX').hex,
};

function SetsChip({ value, status, label }: { value: string; status: RangeStatus; label?: string }) {
  const hex = status === 'NO_TARGET' ? null : STATUS_HEX[status];
  return (
    <Box
      component="span"
      data-status={status}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 4,
        minWidth: 34,
        padding: '2px 8px',
        borderRadius: 999,
        fontSize: 13,
        fontWeight: 600,
        whiteSpace: 'nowrap',
        color: BRAND.textPrimary,
        background: hex ? `${hex}33` : 'transparent',
        border: `1px solid ${hex ?? BRAND.border}`,
      }}
    >
      {value}
      {label && <span style={{ fontWeight: 400, fontSize: 11 }}>{label}</span>}
    </Box>
  );
}

function Legend() {
  const items: [Exclude<RangeStatus, 'NO_TARGET'>, string][] = [
    ['WITHIN', 'dentro da meta'],
    ['BELOW', 'abaixo do mínimo'],
    ['ABOVE', 'acima do máximo'],
  ];
  return (
    <Group gap="md" mt="sm" wrap="wrap">
      {items.map(([status, label]) => (
        <Group key={status} gap={6} wrap="nowrap">
          <Box w={12} h={12} style={{ borderRadius: 999, background: STATUS_HEX[status] }} />
          <Text size="xs" c={BRAND.textMuted}>
            {label}
          </Text>
        </Group>
      ))}
      <Group gap={6} wrap="nowrap">
        <Box w={12} h={12} style={{ borderRadius: 999, border: `1px solid ${BRAND.border}` }} />
        <Text size="xs" c={BRAND.textMuted}>
          sem meta
        </Text>
      </Group>
      <Group gap={6} wrap="nowrap">
        <IconFlame size={14} color={STATUS_HEX.BELOW} />
        <Text size="xs" c={BRAND.textMuted}>
          descanso insuficiente entre dias consecutivos
        </Text>
      </Group>
    </Group>
  );
}

function ActivationTableRow({
  row,
  days,
  pairs,
}: {
  row: ActivationRow;
  days: string[];
  pairs: ConsecutivePair[];
}) {
  const insufficientByDay = insufficientPairByDay(pairs);
  const insufficient = pairs.filter((pair) => pair.item.insufficient_recovery);
  return (
    <Table.Tr>
      <Table.Td>
        <Group gap={6} wrap="nowrap">
          <div>
            <Text size="sm" c={BRAND.textPrimary}>
              {row.group}
            </Text>
            <Text size="xs" c="dimmed">
              {row.volumeClassName ?? 'sem macro-grupo'} · {row.daysTrained}x
              {row.perSession != null && (
                <Tooltip label={sessionTooltip(row)} multiline w={280} withArrow>
                  <span
                    data-session-status={row.sessionStatus}
                    style={{
                      color: row.sessionStatus === 'NO_TARGET' ? undefined : STATUS_HEX[row.sessionStatus],
                    }}
                  >
                    {' · '}
                    {perSessionLabel(row)}
                  </span>
                </Tooltip>
              )}
            </Text>
          </div>
          {insufficient.length > 0 && (
            <Tooltip label={insufficient.map(recoveryLabel).join(' · ')} multiline w={320} withArrow>
              <ThemeIcon color="red" variant="light" radius="xl" size="sm" aria-label="Descanso insuficiente">
                <IconFlame size={14} />
              </ThemeIcon>
            </Tooltip>
          )}
        </Group>
      </Table.Td>
      {row.days.map((cell, index) => {
        const recovery = insufficientByDay.get(index);
        const label = recovery ? `${dayTooltip(cell, row)} · ${recoveryLabel(recovery)}` : dayTooltip(cell, row);
        return (
          <Table.Td key={days[index]} ta="center">
            <Tooltip label={label} multiline w={280} withArrow>
              {cell.status ? (
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 2 }}>
                  <SetsChip value={formatDecimal(cell.directSets)} status={cell.status} />
                  {recovery && <IconFlame size={12} color={STATUS_HEX.BELOW} aria-label="Descanso insuficiente" />}
                </span>
              ) : (
                <Text component="span" size="sm" c="dimmed">
                  ·
                </Text>
              )}
            </Tooltip>
          </Table.Td>
        );
      })}
      <Table.Td ta="center">
        <Tooltip label={weeklyTooltip(row)} multiline w={280} withArrow>
          <span style={{ display: 'inline-flex', flexDirection: 'column', alignItems: 'center', gap: 2 }}>
            <SetsChip value={formatDecimal(row.weeklyTotal)} status={row.weeklyStatus} />
            <Text component="span" size="xs" c={BRAND.textMuted} style={{ whiteSpace: 'nowrap' }}>
              {WEEKLY_STATUS_LABEL[row.weeklyStatus]}
            </Text>
          </span>
        </Tooltip>
      </Table.Td>
    </Table.Tr>
  );
}

export function MuscleActivation7d({ today }: { today?: Date }) {
  const activationQuery = useQuery({
    queryKey: ACTIVATION_QUERY_KEY,
    queryFn: () => apiFetch<MethodologyRealizedResponse>('/analytics/methodology-realized'),
  });
  const recoveryQuery = useQuery({
    queryKey: RECOVERY_CHECK_QUERY_KEY,
    queryFn: () =>
      apiFetch<MuscleRecoveryConsecutiveCheckResponse>(
        `/analytics/muscle-recovery-consecutive-check?days=${ACTIVATION_WINDOW_DAYS}`,
      ),
  });

  const todayKey = today ? today.getTime() : null;
  const responseDays = activationQuery.data?.days;
  const days = useMemo(
    () => responseDays ?? buildLast7Days(todayKey != null ? new Date(todayKey) : undefined),
    [responseDays, todayKey],
  );
  const rows = useMemo(() => toActivationRows(activationQuery.data?.items ?? []), [activationQuery.data]);
  const pairsByBlock = useMemo(
    () => consecutivePairsByBlock(recoveryQuery.data?.items ?? [], days, rows),
    [recoveryQuery.data, days, rows],
  );

  const loading = activationQuery.isPending;

  return (
    <Paper withBorder radius="lg" p="md" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
      <Title order={4} c={BRAND.textPrimary}>
        Ativação muscular (últimos 7 dias)
      </Title>
      <Text size="sm" c={BRAND.textMuted} mb="sm">
        Séries <strong>diretas</strong> por grupo e dia, comparadas com a faixa por treino do macro-grupo na
        frequência real da semana (1x, 2x ou 3x+). Ao lado da frequência, a média por treino — a leitura da
        metodologia. A última coluna soma os 7 dias contra a meta semanal.
      </Text>

      {activationQuery.isError && <ErrorState error={activationQuery.error} />}
      {recoveryQuery.isError && (
        <Alert color="yellow" icon={<IconAlertTriangle size={18} />} mb="sm">
          Não foi possível consultar a recuperação entre dias consecutivos — a grade aparece sem esse destaque.
        </Alert>
      )}

      {loading && !activationQuery.isError && <DashboardSkeleton rows={5} />}

      {!loading && !activationQuery.isError && rows.length === 0 && (
        <EmptyState
          title="Nenhum grupo treinado nos últimos 7 dias"
          description="Nenhuma série com grupo muscular mapeado na semana, e nenhum macro-grupo com meta semanal."
        />
      )}

      {!loading && rows.length > 0 && (
        <>
          <Table.ScrollContainer minWidth={760}>
            <Table striped highlightOnHover withTableBorder verticalSpacing="xs">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Grupo muscular</Table.Th>
                  {days.map((day) => (
                    <Table.Th key={day} ta="center">
                      {formatDayMonth(day)}
                    </Table.Th>
                  ))}
                  <Table.Th ta="center">Semana</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {rows.map((row) => (
                  <ActivationTableRow key={row.group} row={row} days={days} pairs={pairsByBlock.get(row.group) ?? []} />
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
          <Legend />
          <Text size="xs" c="dimmed" mt={6}>
            Séries diretas = exercícios em que o grupo é o alvo principal, sem ponderação (unidade das metas dos
            macro-grupos). O tooltip mostra também as séries equivalentes, que somam o trabalho indireto.
          </Text>
        </>
      )}
    </Paper>
  );
}
