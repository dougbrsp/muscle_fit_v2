'use client';

import { Select } from '@mantine/core';
import {
  itemGoalFromSelect,
  itemGoalSelectData,
  itemGoalSelectValue,
  type TrainingGoalsResponse,
} from '@/lib/training-goals';

// M97 F3: override do objetivo do Modelo num item do slot (herdar / cada
// objetivo de `GET /training-goals`). `null` no estado = herda.
export function ItemGoalSelect({
  trainingGoals,
  templateGoal,
  value,
  onChange,
}: {
  trainingGoals?: TrainingGoalsResponse;
  templateGoal: string | null;
  value: string | null;
  onChange: (override: string | null) => void;
}) {
  if (!trainingGoals) return null;
  return (
    <Select
      size="xs"
      label="Objetivo"
      data={itemGoalSelectData(trainingGoals, templateGoal)}
      value={itemGoalSelectValue(trainingGoals, value)}
      allowDeselect={false}
      onChange={(v) => onChange(itemGoalFromSelect(trainingGoals, v))}
      data-testid="item-goal-select"
    />
  );
}
