'use client';

import { ActionIcon, Badge, Card, Group, Stack, Table, Text, Tooltip } from '@mantine/core';
import { IconAlertTriangle, IconPencil, IconTrash } from '@tabler/icons-react';
import { FieldSourcesIcon } from './FieldSourcesNote';
import {
  AI_ESTIMATE_SOURCE,
  ITEM_NUTRIENT_FIELDS,
  MEAL_NATURE_OPTIONS,
  MEAL_SLOT_OPTIONS,
  NUTRIENT_FIELDS,
  UNIT_OPTIONS,
  nutritionLabel,
} from '@/lib/nutrition-constants';
import { formatNumberOrDash } from '@/lib/format';
import { formatTotal, formatWithUnit } from '@/lib/nutrition-format';
import type { Meal, MealItem, NutritionTotals } from '@/lib/nutrition-types';
import { BRAND } from '@/theme';

// Colunas da tabela de itens: energia e os três macros do dono (`item_nutrient_fields`); fibra e sódio ficam no total.
const COLUNAS = ITEM_NUTRIENT_FIELDS;

/** Selos da natureza dos totais (calculado do catálogo, estimado, misto) e de total incompleto. */
export function TotalsBadges({ totals }: { totals: NutritionTotals }) {
  return (
    <Group gap={4}>
      {totals.nature && (
        <Badge size="xs" variant="light" color={totals.nature === 'DERIVED' ? 'brand' : 'yellow'}>
          {nutritionLabel(MEAL_NATURE_OPTIONS, totals.nature)}
        </Badge>
      )}
      {!totals.complete && (
        <Tooltip label={totals.limitations.map((item) => item.label).join(' ')} multiline w={320} withArrow>
          <Badge size="xs" variant="light" color="yellow" leftSection={<IconAlertTriangle size={10} />}>
            total incompleto
          </Badge>
        </Tooltip>
      )}
    </Group>
  );
}

/** Totais por nutriente: o campo incompleto aparece como mínimo (`≥`). */
export function TotalsLine({ totals }: { totals: NutritionTotals }) {
  return (
    <Group gap="md" wrap="wrap">
      {NUTRIENT_FIELDS.map((campo) => (
        <Text key={campo.code} size="sm" c={BRAND.textMuted}>
          {campo.label}:{' '}
          <b>
            {formatTotal(
              totals.nutrients[campo.code],
              campo.unit,
              !totals.incomplete_fields.includes(campo.code),
              campo.unit === 'kcal' ? 0 : 1,
            )}
          </b>
        </Text>
      ))}
    </Group>
  );
}

function quantidade(item: MealItem): string {
  const unidade = nutritionLabel(UNIT_OPTIONS, item.unit) ?? item.unit;
  const porcao = item.portion_label ? ` (${item.portion_label})` : '';
  return `${formatNumberOrDash(item.quantity, 2)} × ${unidade}${porcao} = ${formatWithUnit(item.amount, item.basis, 0)}`;
}

/** Itens da refeição, com os nutrientes congelados na gravação ou do preview. */
export function MealItemsTable({ items }: { items: MealItem[] }) {
  return (
    <Table.ScrollContainer minWidth={560}>
      <Table verticalSpacing={4} fz="sm">
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Alimento</Table.Th>
            <Table.Th>Quantidade</Table.Th>
            {COLUNAS.map((campo) => (
              <Table.Th key={campo.code} ta="right">
                {campo.label}
              </Table.Th>
            ))}
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {items.map((item) => (
            <Table.Tr key={item.id ?? `novo-${item.position}`}>
              <Table.Td>
                <Group gap={4} wrap="nowrap">
                  <Text size="sm" c={BRAND.textPrimary}>
                    {item.label}
                  </Text>
                  {item.food_source === AI_ESTIMATE_SOURCE && (
                    // Item estimado pela IA (L5b): selo com o rótulo da natureza do dono.
                    <Badge size="xs" variant="light" color="yellow">
                      {nutritionLabel(MEAL_NATURE_OPTIONS, item.nature)}
                    </Badge>
                  )}
                  {item.limitations.length > 0 && (
                    <Tooltip label={item.limitations.map((l) => l.label).join(' ')} multiline w={340} withArrow>
                      <IconAlertTriangle size={14} color="var(--mantine-color-yellow-5)" aria-label="Avisos do item" />
                    </Tooltip>
                  )}
                  <FieldSourcesIcon sources={item.field_sources} />
                </Group>
              </Table.Td>
              <Table.Td>
                <Text size="xs" c={BRAND.textMuted}>
                  {quantidade(item)}
                </Text>
              </Table.Td>
              {COLUNAS.map((campo) => (
                <Table.Td key={campo.code} ta="right">
                  {formatWithUnit(item.nutrients[campo.code], campo.unit, campo.unit === 'kcal' ? 0 : 1)}
                </Table.Td>
              ))}
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}

type Props = {
  meal: Meal;
  onEdit: () => void;
  onDelete: () => void;
  deleting: boolean;
};

/** Uma refeição do diário: slot, hora, itens e totais (natureza e incompleto). */
export function MealCard({ meal, onEdit, onDelete, deleting }: Props) {
  return (
    <Card withBorder radius="md" bg={BRAND.bgElevated}>
      <Stack gap="xs">
        <Group justify="space-between" wrap="nowrap">
          <Group gap="xs">
            <Text fw={700} c={BRAND.textPrimary}>
              {nutritionLabel(MEAL_SLOT_OPTIONS, meal.meal_slot)}
            </Text>
            {meal.eaten_at && (
              <Text size="sm" c={BRAND.textMuted}>
                {meal.eaten_at}
              </Text>
            )}
            <TotalsBadges totals={meal.totals} />
          </Group>
          <Group gap={4} wrap="nowrap">
            <Tooltip label="Editar refeição">
              <ActionIcon variant="subtle" color="brand" aria-label="Editar refeição" onClick={onEdit}>
                <IconPencil size={16} />
              </ActionIcon>
            </Tooltip>
            <Tooltip label="Excluir refeição">
              <ActionIcon variant="subtle" color="red" aria-label="Excluir refeição" loading={deleting} onClick={onDelete}>
                <IconTrash size={16} />
              </ActionIcon>
            </Tooltip>
          </Group>
        </Group>
        <MealItemsTable items={meal.items} />
        <TotalsLine totals={meal.totals} />
        {meal.notes && (
          <Text size="xs" c="dimmed">
            {meal.notes}
          </Text>
        )}
      </Stack>
    </Card>
  );
}
