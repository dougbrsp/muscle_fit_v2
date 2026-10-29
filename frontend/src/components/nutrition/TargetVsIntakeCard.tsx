'use client';

import { Badge, Card, Group, Progress, Stack, Text, Title, Tooltip } from '@mantine/core';
import { IconHistory, IconLock } from '@tabler/icons-react';
import { NutritionLimitations } from './NutritionLimitations';
import { nutrientField, nutritionNotice } from '@/lib/nutrition-constants';
import { formatKcal, formatSignedKcal, formatTotal, formatWithUnit, shareToPercent } from '@/lib/nutrition-format';
import type { BalanceRow, NutritionDay } from '@/lib/nutrition-types';
import { BRAND } from '@/theme';

// Largura máxima da barra (100% da meta). Só apresentação: a fração vem do motor
// (`weekly_budget.day_balance`) e a tela não julga se passar da meta é bom ou ruim.
const BARRA_CHEIA = 100;

function BalanceLine({ row }: { row: BalanceRow }) {
  const campo = nutrientField(row.field);
  const unidade = campo?.unit ?? '';
  const percentual = shareToPercent(row.share);
  return (
    <Stack gap={2}>
      <Group justify="space-between" wrap="nowrap">
        <Text size="sm" c={BRAND.textPrimary} fw={600}>
          {campo?.label ?? row.field}
        </Text>
        <Text size="sm" c={BRAND.textMuted}>
          {formatTotal(row.intake, unidade, row.intake_complete)} de {formatWithUnit(row.target, unidade, 0)}
        </Text>
      </Group>
      <Progress
        value={percentual == null ? 0 : Math.min(percentual, BARRA_CHEIA)}
        color="brand"
        size="sm"
        aria-label={`${campo?.label ?? row.field}: ${percentual == null ? 'sem cálculo' : `${percentual}% da meta`}`}
      />
      {row.remaining != null && (
        <Text size="xs" c="dimmed">
          Saldo: {formatWithUnit(row.remaining, unidade, 0)}
        </Text>
      )}
    </Stack>
  );
}

/**
 * Meta do dia × ingestão, por nutriente, com o balanço energético. Tudo vem do
 * motor (`weekly_budget.day_balance`); o motivo de um saldo ausente (sem meta,
 * sem refeição, total incompleto) vem nas limitações rotuladas pela API.
 */
export function TargetVsIntakeCard({ day }: { day: NutritionDay }) {
  const meta = day.target;
  return (
    <Card withBorder radius="md" bg={BRAND.bgSurface}>
      <Stack gap="sm">
        <Group justify="space-between" wrap="wrap">
          <Title order={4} c={BRAND.textPrimary}>
            Meta × ingestão
          </Title>
          <Group gap={6}>
            {meta?.frozen && (
              <Tooltip label={nutritionNotice('target_frozen')} multiline w={300} withArrow>
                <Badge size="sm" color="gray" variant="light" leftSection={<IconLock size={12} />}>
                  congelada
                </Badge>
              </Tooltip>
            )}
            {meta?.backfilled && (
              <Tooltip label={nutritionNotice('target_backfilled')} multiline w={320} withArrow>
                <Badge size="sm" color="yellow" variant="light" leftSection={<IconHistory size={12} />}>
                  calculada depois
                </Badge>
              </Tooltip>
            )}
          </Group>
        </Group>
        <NutritionLimitations items={day.target_notices} title="Meta do dia" tone="info" />
        {day.balance.rows.map((row) => (
          <BalanceLine key={row.field} row={row} />
        ))}
        <Group gap="lg" wrap="wrap">
          <Text size="sm" c={BRAND.textMuted}>
            Gasto do dia (TDEE): <b>{formatKcal(day.balance.tdee_kcal)}</b>
          </Text>
          <Text size="sm" c={BRAND.textMuted}>
            Balanço (ingestão − gasto): <b>{formatSignedKcal(day.balance.energy_balance_kcal)}</b>
          </Text>
        </Group>
        <NutritionLimitations items={day.balance.limitations} title="Sobre o balanço" tone="info" />
        {meta && <NutritionLimitations items={meta.limitations} title="Sobre a meta" tone="info" />}
      </Stack>
    </Card>
  );
}
