'use client';

import { Group, Text, Tooltip } from '@mantine/core';
import { IconArrowDownRight, IconArrowUpRight, IconBan, IconMinus } from '@tabler/icons-react';
import { CHART_COLORS } from '@/components/charts/chartTheme';
import type { ProgressionSessionEvolution } from '@/lib/dashboard-v2-types';
import { sessionEvolutionView, type SessionEvolutionKind } from '@/lib/exercise-progress';
import { BRAND } from '@/theme';

const ICONS: Record<Exclude<SessionEvolutionKind, 'none'>, typeof IconMinus> = {
  up: IconArrowUpRight,
  down: IconArrowDownRight,
  flat: IconMinus,
  not_comparable: IconBan,
};

const COLORS: Record<SessionEvolutionKind, string> = {
  up: CHART_COLORS.positive,
  down: CHART_COLORS.negative,
  flat: BRAND.textMuted,
  not_comparable: BRAND.textMuted,
  none: BRAND.textMuted,
};

/**
 * Seta "Evolução" de uma sessão de `/dashboard/progressao`.
 *
 * Só desenha: o status vem PRONTO do backend (`items[].evolution`,
 * `compare_exposures` contra a exposição anterior de mesma chave — a mesma
 * regra da home e do drill-down). Nenhuma comparação de números aqui.
 */
export function TrendBadge({ evolution }: { evolution: ProgressionSessionEvolution | null | undefined }) {
  const view = sessionEvolutionView(evolution);
  const color = COLORS[view.kind];

  if (view.kind === 'none') {
    return (
      <Text size="sm" c={color}>
        {view.label}
      </Text>
    );
  }

  const Icon = ICONS[view.kind];
  const content = (
    <Group gap={4} wrap="nowrap">
      <Icon size={16} color={color} aria-hidden />
      <Text size="sm" c={color} fw={view.kind === 'up' || view.kind === 'down' ? 600 : 400}>
        {view.label}
      </Text>
    </Group>
  );

  return view.tooltip ? (
    <Tooltip label={view.tooltip} multiline w={300} withArrow>
      {content}
    </Tooltip>
  ) : (
    content
  );
}
