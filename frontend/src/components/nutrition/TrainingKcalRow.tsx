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
  const extra = training.extra_activity;
  const temExtra = extra !== undefined && extra.kcal > 0;
  const soCardio = temExtra && !training.is_training_day;
  const total = training.activity_kcal;
  return (
    <Card withBorder radius="md" bg={BRAND.bgSurface}>
      <Stack gap="xs">
        <Title order={4} c={BRAND.textPrimary}>
          {soCardio ? 'Atividades do dia' : 'Treino do dia'}
        </Title>
        {training.tdee_basis_note && (
          <Text size="sm" c="yellow" data-testid="gasto-fallback" role="status">
            {training.tdee_basis_note}
          </Text>
        )}
        {temExtra && total && (
          <Text size="sm" fw={600} c={BRAND.textPrimary} data-testid="gasto-atividade">
            {soCardio
              ? `Cardio e caminhadas ${formatKcal(total.extra_kcal)}`
              : total.training_kcal != null && total.total_kcal != null
                ? `Treino ${formatKcal(total.training_kcal)} + Cardio e caminhadas ${formatKcal(total.extra_kcal)} = ${formatKcal(total.total_kcal)}`
                : `Cardio e caminhadas ${formatKcal(total.extra_kcal)}`}
          </Text>
        )}
        {temExtra &&
          extra.items.map((item, i) => (
            <Group key={`${item.label}-${i}`} gap="xs" wrap="wrap">
              <Text size="sm" c={BRAND.textMuted}>
                {item.label ?? 'Atividade'} · {formatKcal(item.calories_kcal)}
              </Text>
            </Group>
          ))}
        {!training.training_known && (
          <Text size="sm" c="dimmed">
            {nutritionNotice('training_unknown_future_day')}
          </Text>
        )}
        {training.training_known && !training.is_training_day && !temExtra && (
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
        {training.comparison?.partial && (
          <Text size="sm" c={BRAND.textMuted} data-testid="treino-parcial">
            {`Usado pelo motor: ${formatKcal(training.comparison.used.kcal)} (informado onde houve kcal e estimado pela duração nas sessões sem kcal).`}
          </Text>
        )}
      </Stack>
    </Card>
  );
}
