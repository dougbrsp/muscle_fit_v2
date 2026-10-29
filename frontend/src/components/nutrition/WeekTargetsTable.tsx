'use client';

import { Badge, Group, Table, Text, Tooltip } from '@mantine/core';
import { IconLock } from '@tabler/icons-react';
import { TRAINING_KCAL_NATURE_OPTIONS, nutritionLabel, nutritionNotice } from '@/lib/nutrition-constants';
import { formatGrams, formatKcal, weekdayShort } from '@/lib/nutrition-format';
import type { NutritionDayTarget } from '@/lib/nutrition-types';
import { formatDateBR } from '@/lib/physical-indicators-types';
import { BRAND } from '@/theme';

export type WeekRow = { local_date: string; target: NutritionDayTarget | null };

function trainingText(target: NutritionDayTarget): string {
  if (!target.training_known) return 'A definir';
  if (!target.is_training_day) return 'Sem treino';
  const natureza = nutritionLabel(TRAINING_KCAL_NATURE_OPTIONS, target.training_kcal_nature);
  return target.training_kcal == null ? 'Treino' : `Treino · ${formatKcal(target.training_kcal)}${natureza ? ` (${natureza})` : ''}`;
}

/** Metas dos 7 dias (segunda primeiro), como o motor calculou ou materializou. */
export function WeekTargetsTable({ rows, today }: { rows: WeekRow[]; today: string }) {
  return (
    <Table.ScrollContainer minWidth={640}>
      <Table verticalSpacing="xs" striped highlightOnHover>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Dia</Table.Th>
            <Table.Th>Treino</Table.Th>
            <Table.Th ta="right">Meta</Table.Th>
            <Table.Th ta="right">Proteína</Table.Th>
            <Table.Th ta="right">Carboidrato</Table.Th>
            <Table.Th ta="right">Gordura</Table.Th>
            <Table.Th ta="right">Fibra</Table.Th>
            <Table.Th>Avisos</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {rows.map(({ local_date, target }) => {
            const hoje = local_date === today;
            return (
              <Table.Tr key={local_date} fw={hoje ? 700 : undefined}>
                <Table.Td>
                  <Group gap={6} wrap="nowrap">
                    <Text size="sm" c={BRAND.textPrimary} fw={hoje ? 700 : 400}>
                      {weekdayShort(local_date)} {formatDateBR(local_date).slice(0, 5)}
                    </Text>
                    {hoje && (
                      <Badge size="xs" color="brand" variant="light">
                        hoje
                      </Badge>
                    )}
                    {target?.frozen && (
                      <Tooltip label={nutritionNotice('target_frozen')} multiline w={300} withArrow>
                        <IconLock size={14} color={BRAND.textMuted} aria-label="Meta congelada" />
                      </Tooltip>
                    )}
                  </Group>
                </Table.Td>
                {target ? (
                  <>
                    <Table.Td>
                      <Text size="sm">{trainingText(target)}</Text>
                    </Table.Td>
                    <Table.Td ta="right">{formatKcal(target.kcal)}</Table.Td>
                    <Table.Td ta="right">{formatGrams(target.protein_g)}</Table.Td>
                    <Table.Td ta="right">{formatGrams(target.carbs_g)}</Table.Td>
                    <Table.Td ta="right">{formatGrams(target.fat_g)}</Table.Td>
                    <Table.Td ta="right">{formatGrams(target.fiber_g)}</Table.Td>
                    <Table.Td>
                      {target.limitations.length > 0 ? (
                        <Tooltip
                          multiline
                          w={320}
                          label={target.limitations.map((item) => item.label).join(' ')}
                        >
                          <Badge size="sm" color="yellow" variant="light">
                            {target.limitations.length === 1 ? '1 aviso' : `${target.limitations.length} avisos`}
                          </Badge>
                        </Tooltip>
                      ) : (
                        <Text size="sm" c="dimmed">
                          -
                        </Text>
                      )}
                    </Table.Td>
                  </>
                ) : (
                  <Table.Td colSpan={7}>
                    <Text size="sm" c="dimmed">
                      Sem meta para este dia.
                    </Text>
                  </Table.Td>
                )}
              </Table.Tr>
            );
          })}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}
