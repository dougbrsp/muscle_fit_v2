'use client';

import { Badge, type BadgeProps } from '@mantine/core';
import { goalBadgeColor, goalOption, useTrainingGoals } from '@/lib/training-goals';

// M97 F3: badge do objetivo de treino (rótulo de `GET /training-goals`).
// `onlyNonDefault`: some quando o objetivo é o default (listas onde quase tudo
// é hipertrofia e o badge só deve chamar atenção para o diferente).
export function TrainingGoalBadge({
  value,
  onlyNonDefault = false,
  ...badgeProps
}: { value: string | null | undefined; onlyNonDefault?: boolean } & Omit<BadgeProps, 'color' | 'children'>) {
  const { data } = useTrainingGoals();
  const option = goalOption(data, value);
  if (!option || (onlyNonDefault && option.is_default)) return null;
  return (
    <Badge
      size="sm"
      variant="light"
      color={goalBadgeColor(option)}
      title="Objetivo de treino"
      data-testid="training-goal-badge"
      {...badgeProps}
    >
      {option.label}
    </Badge>
  );
}
