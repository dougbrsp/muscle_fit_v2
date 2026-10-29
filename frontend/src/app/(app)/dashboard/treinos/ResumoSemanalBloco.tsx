'use client';

import { Group, Paper, SimpleGrid, Text } from '@mantine/core';
import { IconBarbell, IconFlame, IconGauge, IconTrendingUp } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { DashboardCardSkeleton, EmptyState, ErrorState } from '@/components/dashboard/states';
import { dashboardKeys, fetchTrainingEvolution } from '@/lib/dashboard-api';
import {
  WEEKLY_SUMMARY_QUERY_DAYS,
  weeklySummaryTiles,
  type WeeklySummaryTile,
} from '@/lib/dashboard-training-analytics';
import { BRAND } from '@/theme';
import { BlocoCard, TILE_STYLE } from './BlocoCard';

const ICONS: Record<WeeklySummaryTile['key'], typeof IconBarbell> = {
  workouts: IconBarbell,
  sets: IconGauge,
  volume: IconTrendingUp,
  high_rpe: IconFlame,
};

/**
 * "Resumo da semana" (herdado de `/treinos`): `weekly_summary` de
 * `GET /analytics/training-evolution`. É sempre a SEMANA CORRENTE — não
 * acompanha o seletor de período (ver `WEEKLY_SUMMARY_QUERY_DAYS`).
 */
export function ResumoSemanalBloco() {
  const consulta = useQuery({
    queryKey: dashboardKeys.trainingEvolution(WEEKLY_SUMMARY_QUERY_DAYS),
    queryFn: () => fetchTrainingEvolution(WEEKLY_SUMMARY_QUERY_DAYS),
  });

  if (consulta.isLoading) return <DashboardCardSkeleton rows={2} />;
  if (consulta.isError) {
    return <ErrorState title="Não foi possível carregar o resumo da semana." error={consulta.error} />;
  }
  const resumo = consulta.data?.weekly_summary;

  return (
    <BlocoCard titulo="Resumo da semana" descricao="Semana corrente — não acompanha o período selecionado.">
      {!resumo ? (
        <EmptyState description="Sem resumo disponível para a semana corrente." />
      ) : (
        <SimpleGrid cols={{ base: 2, lg: 4 }} spacing="sm">
          {weeklySummaryTiles(resumo).map((tile) => {
            const Icon = ICONS[tile.key];
            return (
              <Paper key={tile.key} withBorder radius="md" p="sm" style={TILE_STYLE}>
                <Group gap={6} mb={4} wrap="nowrap">
                  <Icon size={16} color={BRAND.orange} />
                  <Text size="xs" c={BRAND.textMuted} tt="uppercase" fw={600}>
                    {tile.label}
                  </Text>
                </Group>
                <Text size="xl" fw={700} c={BRAND.textPrimary}>
                  {tile.value}
                </Text>
                <Text size="xs" c={BRAND.textMuted}>
                  {tile.hint}
                </Text>
              </Paper>
            );
          })}
        </SimpleGrid>
      )}
    </BlocoCard>
  );
}
