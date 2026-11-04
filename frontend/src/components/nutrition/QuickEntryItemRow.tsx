'use client';

import { ActionIcon, Badge, Group, NumberInput, Stack, Text, TextInput } from '@mantine/core';
import { IconTrash } from '@tabler/icons-react';
import { MEAL_NATURE_OPTIONS, nutritionLabel } from '@/lib/nutrition-constants';
import type { MealEditorItem } from '@/lib/nutrition-meal-form';
import { QUICK_ENTRY_NAME_MAX_CHARS, QUICK_ENTRY_NATURE, QUICK_ENTRY_NUMERIC_FIELDS, type QuickEntryDraft } from '@/lib/nutrition-quick-entry';
import { BRAND } from '@/theme';

/**
 * Item de lançamento rápido no editor: os 6 valores editáveis (nome, peso e os 4 nutrientes),
 * sem busca, porção nem unidade. Mantido = o editor manda só o `id`; alterado = `quick_entry` com o `id`.
 */
export function QuickEntryItemRow({
  item,
  draft,
  onChange,
  onRemove,
}: {
  item: MealEditorItem;
  draft: QuickEntryDraft;
  onChange: (item: MealEditorItem) => void;
  onRemove: () => void;
}) {
  const mudar = (novo: QuickEntryDraft) => onChange({ ...item, quick: novo, label: novo.name });
  return (
    <Stack gap={4}>
      <Group gap="xs">
        <Text size="sm" fw={600} c={BRAND.textPrimary}>
          Lançamento rápido
        </Text>
        <Badge size="xs" variant="light" color="gray">
          {nutritionLabel(MEAL_NATURE_OPTIONS, QUICK_ENTRY_NATURE)}
        </Badge>
      </Group>
      <Group gap="xs" align="flex-end" wrap="wrap">
        <TextInput
          label="Nome"
          value={draft.name}
          onChange={(event) => mudar({ ...draft, name: event.currentTarget.value })}
          maxLength={QUICK_ENTRY_NAME_MAX_CHARS}
          style={{ flex: '1 1 200px' }}
        />
        {QUICK_ENTRY_NUMERIC_FIELDS.map((campo) => (
          <NumberInput
            key={campo.code}
            label={`${campo.label} (${campo.unit})`}
            value={draft.values[campo.code] ?? ''}
            onChange={(valor) => mudar({ ...draft, values: { ...draft.values, [campo.code]: valor } })}
            min={0}
            decimalScale={2}
            decimalSeparator=","
            hideControls
            inputMode="decimal"
            w={120}
          />
        ))}
        <ActionIcon variant="subtle" color="red" aria-label={`Remover ${item.label}`} onClick={onRemove} mb={4}>
          <IconTrash size={16} />
        </ActionIcon>
      </Group>
    </Stack>
  );
}
