'use client';

import { Group, NumberInput, Paper, Stack, Text, Textarea, Title } from '@mantine/core';
import { RULE_LIMITS, toDraftNumber, type DraftNumber } from '@/lib/next-set-load-rules';
import { BRAND } from '@/theme';

/** Piso e teto de repetições (resolvem os valores "piso"/"teto" das regras) e notas da versão. */
export function GlobalParamsCard({
  repsFloor,
  repsCeiling,
  notes,
  onChange,
}: {
  repsFloor: DraftNumber;
  repsCeiling: DraftNumber;
  notes: string;
  onChange: (patch: { reps_floor?: DraftNumber; reps_ceiling?: DraftNumber; notes?: string }) => void;
}) {
  return (
    <Paper withBorder radius="md" p="md">
      <Stack gap="sm">
        <div>
          <Title order={4}>Parâmetros globais</Title>
          <Text size="sm" c={BRAND.textMuted}>
            O piso e o teto de repetições são os valores de &quot;piso&quot; e &quot;teto&quot; nas condições de reps.
          </Text>
        </div>
        <Group gap="md" align="flex-end">
          <NumberInput
            label="Piso de reps"
            w={140}
            min={RULE_LIMITS.reps_floor_min}
            max={RULE_LIMITS.reps_ceiling_max}
            decimalScale={0}
            value={repsFloor}
            onChange={(raw) => onChange({ reps_floor: toDraftNumber(raw) })}
          />
          <NumberInput
            label="Teto de reps"
            w={140}
            min={RULE_LIMITS.reps_floor_min}
            max={RULE_LIMITS.reps_ceiling_max}
            decimalScale={0}
            value={repsCeiling}
            onChange={(raw) => onChange({ reps_ceiling: toDraftNumber(raw) })}
          />
          <Textarea
            label="Notas desta versão (opcional)"
            style={{ flex: 1, minWidth: 220 }}
            autosize
            minRows={1}
            maxRows={4}
            value={notes}
            onChange={(event) => onChange({ notes: event.currentTarget.value })}
          />
        </Group>
      </Stack>
    </Paper>
  );
}
