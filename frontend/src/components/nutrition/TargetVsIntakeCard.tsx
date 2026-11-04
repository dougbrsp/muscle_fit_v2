'use client';

import { Badge, Card, Group, Paper, Progress, SimpleGrid, Stack, Text, Title, Tooltip } from '@mantine/core';
import { IconHistory, IconLock } from '@tabler/icons-react';
import dynamic from 'next/dynamic';
import { ChartPlaceholder } from '@/components/charts/ChartPlaceholder';
import { CHART_COLORS } from '@/components/charts/chartTheme';
import { NutritionLimitations } from './NutritionLimitations';
import { nutrientField, nutritionNotice } from '@/lib/nutrition-constants';
import { formatKcal, formatSignedKcal, formatTotal, formatWithUnit, shareToPercent } from '@/lib/nutrition-format';
import { macroRows, ringParts, type RingPart } from '@/lib/nutrition-dashboard';
import type { BalanceRow, NutritionDay } from '@/lib/nutrition-types';
import { usePresentationSlot } from '@/lib/presentation-slots';
import { BRAND } from '@/theme';

// COD-14: recharts sob demanda; o placeholder tem a altura da rosca (`RING_SIZE` de NutritionCharts).
const RING_HEIGHT = 132;
const MacroRing = dynamic(() => import('./NutritionCharts').then((m) => m.MacroRing), {
  ssr: false,
  loading: () => <ChartPlaceholder height={RING_HEIGHT} />,
});

// Mesmas cores das fatias de `NutritionCharts` (consumido, a consumir, acima da meta): legenda do cartão.
const LEGEND: ReadonlyArray<{ key: RingPart['key']; label: string; color: string }> = [
  { key: 'within', label: 'Consumido', color: CHART_COLORS.primary },
  { key: 'to_consume', label: 'Restante', color: CHART_COLORS.compare },
  { key: 'excess', label: 'Acima da meta', color: CHART_COLORS.secondary },
];

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
 * Um macro do dia (kcal, proteína, carboidrato, gordura): rosca consumido × a consumir e os mesmos
 * números em texto (consumido, meta, falta/acima). Tudo vem do motor (`BalanceRow`): a tela não calcula saldo.
 */
function MacroTile({ row }: { row: BalanceRow }) {
  const campo = nutrientField(row.field);
  const unidade = campo?.unit ?? '';
  const nome = campo?.label ?? row.field;
  const partes = ringParts(row);
  return (
    <Paper withBorder radius="md" p="sm" bg={BRAND.bgElevated} aria-label={nome}>
      <Stack gap={4}>
        <Text size="sm" fw={700} c={BRAND.textPrimary}>
          {nome}
        </Text>
        {partes ? (
          <div role="img" aria-label={`${nome}: ${partes.map((p) => `${p.label} ${formatWithUnit(p.value, unidade, 0)}`).join(', ')}`}>
            <MacroRing parts={partes} unit={unidade} />
          </div>
        ) : (
          <Text size="xs" c="dimmed" mih={RING_HEIGHT}>
            Sem gráfico: falta meta ou o total do dia está incompleto.
          </Text>
        )}
        <Text size="xs" c={BRAND.textMuted}>
          Consumido: <b>{formatTotal(row.intake ?? row.within_target, unidade, row.intake == null || row.intake_complete)}</b>
        </Text>
        {row.target != null ? (
          <>
            <Text size="xs" c={BRAND.textMuted}>
              Meta: <b>{formatWithUnit(row.target, unidade, 0)}</b>
            </Text>
            {row.excess != null && row.excess > 0 ? (
              <Text size="xs" c={BRAND.textMuted}>
                Acima da meta: <b>{formatWithUnit(row.excess, unidade, 0)}</b>
              </Text>
            ) : (
              <Text size="xs" c={BRAND.textMuted}>
                Restante: <b>{formatWithUnit(row.to_consume, unidade, 0)}</b>
              </Text>
            )}
          </>
        ) : (
          <Text size="xs" c="dimmed">
            Sem meta para este dia.
          </Text>
        )}
      </Stack>
    </Paper>
  );
}

/**
 * Meta do dia × ingestão, por nutriente, com o balanço energético. Tudo vem do
 * motor (`weekly_budget.day_balance`); o motivo de um saldo ausente (sem meta,
 * sem refeição, total incompleto) vem nas limitações rotuladas pela API.
 */
export function TargetVsIntakeCard({ day }: { day: NutritionDay }) {
  const MobileView = usePresentationSlot('targetVsIntake');
  if (MobileView) return <MobileView day={day} />;
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
        <SimpleGrid cols={{ base: 2, md: 4 }} spacing="xs">
          {macroRows(day.balance.rows).map((row) => (
            <MacroTile key={row.field} row={row} />
          ))}
        </SimpleGrid>
        <Group gap="md" wrap="wrap" aria-label="Legenda do gráfico">
          {LEGEND.map((item) => (
            <Group key={item.key} gap={6} wrap="nowrap">
              <span aria-hidden style={{ width: 10, height: 10, borderRadius: 3, background: item.color, display: 'inline-block' }} />
              <Text size="xs" c={BRAND.textMuted}>
                {item.label}
              </Text>
            </Group>
          ))}
        </Group>
        {day.balance.rows
          .filter((row) => !macroRows(day.balance.rows).includes(row))
          .map((row) => (
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
