'use client';

import { Badge, Card, Group, Stack, Text, Title } from '@mantine/core';
import { TRAINING_KCAL_NATURE_OPTIONS, nutritionLabel, nutritionNotice } from '@/lib/nutrition-constants';
import { formatDurationMinutes, formatKcal } from '@/lib/nutrition-format';
import type { DayTraining, TrainingKcalSide } from '@/lib/nutrition-types';
import { BRAND } from '@/theme';

function Valor({ lado }: { lado: TrainingKcalSide }) {
  return (
    <Stack gap={0}>
      <Text size="xs" c="dimmed">
        {nutritionLabel(TRAINING_KCAL_NATURE_OPTIONS, lado.kcal_nature)}
      </Text>
      <Group gap={6}>
        <Text size="sm" fw={700} c={BRAND.textPrimary}>
          {formatKcal(lado.value.kcal)}
        </Text>
        {lado.is_used && (
          <Badge size="xs" color="brand" variant="light">
            prioridade do motor
          </Badge>
        )}
      </Group>
    </Stack>
  );
}

/**
 * kcal do treino do dia: "informado × estimado". A informada vem das sessões
 * (com a proveniência manual × Intervals do dono `calories_source`); a
 * estimativa por MET e qual das duas o motor usa vêm prontas
 * (`energy_expenditure.compare_training_kcal`). A tela só exibe.
 */
export function TrainingKcalRow({ training }: { training: DayTraining }) {
  return (
    <Card withBorder radius="md" bg={BRAND.bgSurface}>
      <Stack gap="xs">
        <Title order={4} c={BRAND.textPrimary}>
          Treino do dia
        </Title>
        {!training.training_known && (
          <Text size="sm" c="dimmed">
            {nutritionNotice('training_unknown_future_day')}
          </Text>
        )}
        {training.training_known && !training.is_training_day && (
          <Text size="sm" c="dimmed">
            Nenhum treino concluído neste dia.
          </Text>
        )}
        {training.sessions.map((sessao) => (
          <Group key={sessao.id} gap="xs" wrap="wrap">
            <Text size="sm" c={BRAND.textPrimary}>
              {sessao.name ?? 'Treino'}
            </Text>
            <Text size="sm" c={BRAND.textMuted}>
              {formatDurationMinutes(sessao.duration_seconds)} · {formatKcal(sessao.calories_kcal)}
            </Text>
            {sessao.calories_source_label && (
              <Badge size="xs" variant="outline" color="gray">
                {sessao.calories_source_label}
              </Badge>
            )}
          </Group>
        ))}
        {training.comparison && (
          <Group gap="xl" wrap="wrap">
            {training.comparison.sides.map((lado) => (
              <Valor key={lado.kcal_nature} lado={lado} />
            ))}
          </Group>
        )}
      </Stack>
    </Card>
  );
}
