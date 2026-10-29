'use client';

import { Alert, Badge, Button, Card, Group, Stack, Text, Title } from '@mantine/core';
import { IconAlertTriangle, IconPhoto } from '@tabler/icons-react';
import type { ReactNode } from 'react';
import type { WeightUnit } from '@/lib/weight-units';
import { laboralSummaryLabel } from '@/lib/workout-mode-laboral';
import type { LaboralPrescription } from '@/lib/workout-plans-types';
import { BRAND } from '@/theme';

/**
 * Exercício laboral (M100): card SÓ LEITURA com o padrão pronto do backend
 * ("3 × 15 · 2 kg"). "Concluir" grava as séries prescritas como `laboral`;
 * "Pular" tira o item da fila sem gravar nada. Sem RPE, sem descanso por
 * série e sem avaliação por série.
 */
export function LaboralExerciseCard({
  exerciseName,
  equipmentName,
  equipmentPhotoUrl,
  prescription,
  weightUnit,
  saving,
  error,
  nextExerciseLine,
  onComplete,
  onSkip,
}: {
  exerciseName: string;
  equipmentName?: string | null;
  equipmentPhotoUrl?: string | null;
  prescription: LaboralPrescription;
  weightUnit: WeightUnit;
  saving: boolean;
  error: string | null;
  nextExerciseLine?: ReactNode;
  onComplete: () => void;
  onSkip: () => void;
}) {
  return (
    <Card
      withBorder
      radius="xl"
      p="lg"
      style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}
      data-testid="laboral-exercise-card"
    >
      <Stack gap="md">
        <Group gap="sm" wrap="nowrap" align="flex-start">
          {equipmentPhotoUrl ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={equipmentPhotoUrl}
              alt={equipmentName || exerciseName}
              width={56}
              height={56}
              style={{ objectFit: 'cover', borderRadius: 12, flexShrink: 0 }}
            />
          ) : (
            <IconPhoto size={56} color={BRAND.textMuted} style={{ flexShrink: 0 }} />
          )}
          <div style={{ minWidth: 0 }}>
            <Badge size="sm" variant="light" color="orange" mb={4}>
              Laboral
            </Badge>
            <Title order={3} c={BRAND.textPrimary}>
              {exerciseName}
            </Title>
            {equipmentName ? (
              <Text size="xs" c={BRAND.textMuted}>
                {equipmentName}
              </Text>
            ) : null}
          </div>
        </Group>

        <Text fw={800} size="xl" c={BRAND.textPrimary} data-testid="laboral-summary">
          {laboralSummaryLabel(prescription, weightUnit)}
        </Text>
        <Text size="xs" c={BRAND.textMuted}>
          Aquecimento/alongamento sem progressão: conta só no tempo de treino.
        </Text>

        {error ? (
          <Alert color="red" variant="light" icon={<IconAlertTriangle size={16} />} py={6}>
            <Text size="xs">{error}</Text>
          </Alert>
        ) : null}

        {nextExerciseLine}

        <Button size="lg" radius="xl" h={56} fullWidth loading={saving} onClick={onComplete} data-testid="laboral-complete">
          Concluir
        </Button>
        <Button
          size="sm"
          radius="xl"
          variant="subtle"
          color="gray"
          disabled={saving}
          onClick={onSkip}
          data-testid="laboral-skip"
        >
          Pular
        </Button>
      </Stack>
    </Card>
  );
}
