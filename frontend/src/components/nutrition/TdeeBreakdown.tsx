'use client';

import { useId } from 'react';
import { Button, Group, Stack, Text } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { formatKcal } from '@/lib/nutrition-format';
import type { NutritionTdeeBreakdown } from '@/lib/nutrition-types';
import { BRAND } from '@/theme';

function Line({ label, value }: { label: string; value: string }) {
  return (
    <Group justify="space-between" wrap="nowrap" gap="xs">
      <Text size="sm" c="dimmed">
        {label}
      </Text>
      <Text size="sm" c={BRAND.textPrimary} ta="right">
        {value}
      </Text>
    </Group>
  );
}

/** Linhas da composição (parcelas na ordem recebida, observações e total). Só exibe o que o motor mandou. */
export function TdeeBreakdownLines({ breakdown }: { breakdown: NutritionTdeeBreakdown }) {
  return (
    <Stack gap={4} data-testid="gasto-composicao-linhas">
      {breakdown.parts.map((parte) => (
        <Line key={parte.code} label={parte.label} value={formatKcal(parte.kcal)} />
      ))}
      {breakdown.notes.map((nota) => (
        <Text key={nota.code} size="xs" c="dimmed" data-testid="gasto-nota">
          {nota.label}
        </Text>
      ))}
      <Line label="Gasto total" value={formatKcal(breakdown.total_kcal)} />
    </Stack>
  );
}

/**
 * Composição do gasto total: só renderiza o que o motor mandou em `tdee_breakdown` (parcelas com rótulo,
 * observações e total; a soma das parcelas é garantida pelo backend). A tela não decide parcelas por modo.
 * Sem `tdee_breakdown` (backend antigo) não há botão e a linha de gasto total permanece como está.
 */
export function TdeeBreakdown({ breakdown }: { breakdown: NutritionTdeeBreakdown | null | undefined }) {
  const [aberto, { toggle }] = useDisclosure(false);
  const painelId = useId();
  if (!breakdown || breakdown.parts.length === 0) return null;
  return (
    <Stack gap={4} data-testid="gasto-composicao">
      <Button
        variant="subtle"
        size="compact-xs"
        onClick={toggle}
        style={{ alignSelf: 'flex-start' }}
        data-testid="gasto-composicao-toggle"
        aria-expanded={aberto}
        aria-controls={painelId}
      >
        {aberto ? 'Ocultar composição do gasto' : 'Ver composição do gasto'}
      </Button>
      <div id={painelId} hidden={!aberto}>
        {aberto && (
          <div style={{ paddingLeft: 'var(--mantine-spacing-sm)' }}>
            <TdeeBreakdownLines breakdown={breakdown} />
          </div>
        )}
      </div>
    </Stack>
  );
}
