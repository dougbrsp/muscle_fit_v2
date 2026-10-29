'use client';

import dynamic from 'next/dynamic';
import { Anchor, Badge, Box, Group, Paper, SimpleGrid, Stack, Text } from '@mantine/core';
import { IconBarbell } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import Link from 'next/link';
import { useMemo } from 'react';
import { ChartPlaceholder } from '@/components/charts/ChartPlaceholder';
import { CHART_LEGEND_STYLE } from '@/components/charts/chartTheme';
import { dashboardKeys, fetchStrengthPanel } from '@/lib/dashboard-api';
import type { DashboardFilters, DashboardWindowQuery } from '@/lib/dashboard-filters';
import { fmtDate, fmtInt, fmtKg } from '@/lib/dashboard-format';
import { exerciseProgressHref } from '@/lib/exercise-progress';
import { strengthPanelRows, topSetCaption, topSetText } from '@/lib/strength-panel';
import { BRAND } from '@/theme';

// COD-14: recharts sob demanda; o placeholder tem a altura do gráfico.
const UnifiedE1rmChart = dynamic(
  () => import('@/components/charts/UnifiedE1rmChart').then((m) => m.UnifiedE1rmChart),
  { ssr: false, loading: () => <ChartPlaceholder height="100%" /> },
);

/**
 * Painel de força (M97 F4): para cada composto principal (seleção do backend,
 * `strength_panel.py`), a tendência do 1RM estimado — linha ÚNICA entre
 * objetivos, com as sessões de força destacadas — e o último top set (carga ×
 * reps). Sem sessão de força na janela, o painel não aparece.
 */
export function StrengthPanel({ window, filters }: { window: DashboardWindowQuery; filters: DashboardFilters }) {
  const query = useQuery({
    queryKey: dashboardKeys.strengthPanel(window),
    queryFn: () => fetchStrengthPanel(window),
    // Painel complementar: erro não derruba a tela (some, como sem dados).
    retry: false,
  });
  const rows = useMemo(() => strengthPanelRows(query.data), [query.data]);
  if (rows.length === 0) return null;

  return (
    <Paper withBorder radius="lg" p="md" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
      <Group gap="xs" mb={4}>
        <IconBarbell size={18} color={BRAND.orange} />
        <Text fw={700} c={BRAND.textPrimary}>
          {query.data?.training_goal_label ? `${query.data.training_goal_label} — ` : ''}compostos principais
        </Text>
      </Group>
      <Text size="xs" c={BRAND.textMuted} mb="sm">
        Os compostos com mais sessões de força no período. 1RM estimado é um só para todos os objetivos (pontos
        destacados = sessões de força); deload fica fora da linha.
      </Text>
      <SimpleGrid cols={{ base: 1, md: 2 }} spacing="md">
        {rows.map((row) => (
          <Stack key={row.exerciseId} gap={4}>
            <Group justify="space-between" wrap="wrap" gap="xs">
              <Anchor component={Link} href={exerciseProgressHref(row.exerciseId, filters)} fw={600} c={BRAND.orange}>
                {row.name}
              </Anchor>
              <Badge size="sm" variant="light" color="red">
                {fmtInt(row.strengthSessions)} sessão(ões) de força
              </Badge>
            </Group>
            <Text size="sm" c={BRAND.textPrimary}>
              1RM estimado: <b>{fmtKg(row.latestE1rm)}</b>
              {row.latestE1rmDate ? ` (${fmtDate(row.latestE1rmDate)})` : ''} · melhor {fmtKg(row.bestE1rm)}
              {row.bestE1rmReliable === false ? ' (pouco confiável)' : ''}
            </Text>
            <Text size="sm" c={BRAND.textPrimary}>
              Último top set: <b>{topSetText(row.lastTopSet)}</b>
              {topSetCaption(row.lastTopSet) ? ` em ${topSetCaption(row.lastTopSet)}` : ''}
            </Text>
            {row.chart.hasSeries && (
              <Box h={140}>
                <UnifiedE1rmChart chart={row.chart} compact />
              </Box>
            )}
          </Stack>
        ))}
      </SimpleGrid>
      <Text size="xs" style={CHART_LEGEND_STYLE} mt="xs">
        1RM estimado (Epley + RIR): estimativa, não medida. Top set = carga de topo × mais repetições nas séries de topo.
      </Text>
    </Paper>
  );
}
