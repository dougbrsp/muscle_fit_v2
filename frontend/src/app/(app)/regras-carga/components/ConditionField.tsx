'use client';

import { Group, NumberInput, Select, Text } from '@mantine/core';
import {
  CONDITION_OP_OPTIONS,
  NO_CONDITION,
  NUMBER_SOURCE,
  emptyCondition,
  isBetween,
  toDraftNumber,
  type ConditionDraft,
  type SelectOption,
  type ValueDraft,
} from '@/lib/next-set-load-rules';

type Bounds = { min: number; max: number };

function ValueField({
  label,
  value,
  sourceOptions,
  bounds,
  decimal,
  onChange,
}: {
  label: string;
  value: ValueDraft;
  sourceOptions: SelectOption[];
  bounds: Bounds;
  decimal: boolean;
  onChange: (value: ValueDraft) => void;
}) {
  return (
    <Group gap={4} wrap="nowrap">
      <Select
        size="xs"
        w={110}
        aria-label={`${label}: tipo do valor`}
        data={sourceOptions}
        value={value.source}
        allowDeselect={false}
        onChange={(source) => source && onChange({ source, number: value.number })}
      />
      {value.source === NUMBER_SOURCE && (
        <NumberInput
          size="xs"
          w={70}
          aria-label={`${label}: valor`}
          min={bounds.min}
          max={bounds.max}
          step={decimal ? 0.5 : 1}
          decimalScale={decimal ? 1 : 0}
          decimalSeparator=","
          value={value.number}
          onChange={(raw) => onChange({ source: value.source, number: toDraftNumber(raw) })}
        />
      )}
    </Group>
  );
}

/**
 * Condição de uma regra (reps ou RPE): operador do vocabulário (ou "sem
 * condição") + número ou token; "entre" pede o segundo valor. Só monta o
 * campo: quem compara é o dono no servidor.
 */
export function ConditionField({
  label,
  condition,
  sourceOptions,
  bounds,
  decimal = false,
  onChange,
}: {
  label: string;
  condition: ConditionDraft | null;
  sourceOptions: SelectOption[];
  bounds: Bounds;
  decimal?: boolean;
  onChange: (condition: ConditionDraft | null) => void;
}) {
  const op = condition?.op ?? NO_CONDITION;
  return (
    <Group gap={4} wrap="nowrap" align="center">
      <Select
        size="xs"
        w={120}
        aria-label={`${label}: operador`}
        data={CONDITION_OP_OPTIONS}
        value={op}
        allowDeselect={false}
        onChange={(next) => {
          if (next === null) return;
          if (next === NO_CONDITION) onChange(null);
          else onChange(condition ? { ...condition, op: next } : emptyCondition(next));
        }}
      />
      {condition && (
        <ValueField
          label={label}
          value={condition.value}
          sourceOptions={sourceOptions}
          bounds={bounds}
          decimal={decimal}
          onChange={(value) => onChange({ ...condition, value })}
        />
      )}
      {condition && isBetween(condition.op) && (
        <>
          <Text size="xs">e</Text>
          <ValueField
            label={`${label} (até)`}
            value={condition.valueTo}
            sourceOptions={sourceOptions}
            bounds={bounds}
            decimal={decimal}
            onChange={(valueTo) => onChange({ ...condition, valueTo })}
          />
        </>
      )}
    </Group>
  );
}
