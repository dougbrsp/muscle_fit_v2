'use client';

import { Badge, Tooltip } from '@mantine/core';
import type { NutritionDayTarget } from '@/lib/nutrition-types';

type Props = {
  target: Pick<NutritionDayTarget, 'nutrition_priority' | 'nutrition_priority_label' | 'nutrition_priority_explanation'> | null | undefined;
};

/**
 * Selo da prioridade do dia (#272). Rótulo e explicação vêm prontos da API; sem rótulo não há selo.
 * Cor única de propósito: a tela não decide nada pelo código da prioridade (só o expõe em `data-priority`).
 */
export function DayPriorityBadge({ target }: Props) {
  const label = target?.nutrition_priority_label;
  if (!label) return null;
  const explanation = target?.nutrition_priority_explanation;
  const badge = (
    <Badge
      size="md"
      color="brand"
      variant="light"
      tt="none"
      data-testid="day-priority-badge"
      data-priority={target?.nutrition_priority ?? undefined}
      tabIndex={explanation ? 0 : undefined}
      role={explanation ? 'note' : undefined}
      aria-label={explanation ? `${label}. ${explanation}` : undefined}
      style={explanation ? { cursor: 'help' } : undefined}
    >
      {label}
    </Badge>
  );
  if (!explanation) return badge;
  return (
    <Tooltip label={explanation} multiline w={280} withArrow events={{ hover: true, focus: true, touch: true }}>
      {badge}
    </Tooltip>
  );
}
