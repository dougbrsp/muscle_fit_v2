'use client';

import { Stack, Text } from '@mantine/core';
import { MealCard } from './MealCard';
import type { Meal } from '@/lib/nutrition-types';

type Props = {
  meals: Meal[];
  onEdit: (meal: Meal) => void;
  onDelete: (meal: Meal) => void;
  deletingId: number | null;
};

/** Refeições do dia na ordem do motor (slot, hora; `diary.meal_sort_key`, já ordenadas pela API). */
export function MealList({ meals, onEdit, onDelete, deletingId }: Props) {
  if (meals.length === 0) {
    return (
      <Text size="sm" c="dimmed">
        Nenhuma refeição registrada neste dia.
      </Text>
    );
  }
  return (
    <Stack gap="sm">
      {meals.map((meal) => (
        <MealCard
          key={meal.id}
          meal={meal}
          onEdit={() => onEdit(meal)}
          onDelete={() => onDelete(meal)}
          deleting={deletingId === meal.id}
        />
      ))}
    </Stack>
  );
}
