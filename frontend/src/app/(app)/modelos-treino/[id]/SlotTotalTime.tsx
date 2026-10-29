'use client';

import { Stack, Text, Tooltip } from '@mantine/core';
import { EXEC_SECONDS_PER_REP, estimateSlotTime, formatRestSeconds } from '@/lib/template-time-estimate';
import type { ItemState } from '@/lib/workout-plan-template-form';
import { BRAND } from '@/theme';

// ---------------------------------------------------------------------------
// "Tempo total de treino" do slot (modo 'equipment_series', Fase 7) —
// descanso + execução, calculado por `estimateSlotTime`. Só exibição: não é
// salvo, nem editável.
export function SlotTotalTime({ items }: { items: ItemState[] }) {
  const estimate = estimateSlotTime(items);
  const { breakdown, itemsWithoutRest } = estimate;
  const lines: Array<[string, number]> = [
    ['Aquecimento', breakdown.warmup],
    ['Ajuste', breakdown.feeler],
    ['Trabalho', breakdown.work],
    ['Técnica avançada', breakdown.technique],
    ['Cardio', breakdown.cardio],
  ];
  const tooltip = (
    <Stack gap={2}>
      {lines
        .filter(([, seconds]) => seconds > 0)
        .map(([label, seconds]) => (
          <Text key={label} size="xs">
            {label}: {formatRestSeconds(seconds)}
          </Text>
        ))}
      <Text size="xs">
        Execução: {formatRestSeconds(breakdown.execSeconds)} · Descanso: {formatRestSeconds(breakdown.restSeconds)}
      </Text>
      <Text size="xs" fs="italic">
        Execução estimada em {EXEC_SECONDS_PER_REP} s por repetição
        {estimate.hasEstimatedReps ? '; repetições vazias contam como 10' : ''}.
      </Text>
    </Stack>
  );
  return (
    <Stack gap={0}>
      <Tooltip label={tooltip} multiline w={260}>
        <Text size="xs" c={BRAND.textMuted} style={{ cursor: 'help' }}>
          Tempo total de treino:{' '}
          <Text span fw={700} c={BRAND.textPrimary}>
            {formatRestSeconds(estimate.totalSeconds)}
          </Text>
        </Text>
      </Tooltip>
      {itemsWithoutRest > 0 && (
        <Text size="xs" c={BRAND.textMuted}>
          {itemsWithoutRest === 1
            ? '1 exercício sem descanso definido (conta 0 s)'
            : `${itemsWithoutRest} exercícios sem descanso definido (contam 0 s)`}
        </Text>
      )}
    </Stack>
  );
}
