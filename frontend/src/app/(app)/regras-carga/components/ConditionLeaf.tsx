'use client';

import { ActionIcon, Box, Checkbox, Group, NumberInput, Select, Text, Tooltip } from '@mantine/core';
import { IconCopy, IconTrash } from '@tabler/icons-react';
import {
  NUMBER_SOURCE,
  VARIABLE_OPTIONS,
  isBetween,
  isEnumVariable,
  leafWithScope,
  leafWithVariable,
  opOptionsFor,
  scopeOptionsFor,
  supportsRampCap,
  toDraftNumber,
  valueBoundsFor,
  valueSourceOptionsFor,
  type LeafDraft,
  type ValueDraft,
} from '@/lib/next-set-load-rules';
import { BRAND } from '@/theme';

const FIELD = { flex: '1 1 120px', minWidth: 0 } as const;

/** Origem (número, token ou valor do enum) + número quando a origem é "número". */
function ValueEditor({
  label,
  variable,
  value,
  onChange,
}: {
  label: string;
  variable: string;
  value: ValueDraft;
  onChange: (value: ValueDraft) => void;
}) {
  const sources = valueSourceOptionsFor(variable);
  const bounds = valueBoundsFor(variable);
  const showSource = sources.length > 1 || isEnumVariable(variable);
  return (
    <Group gap={4} wrap="wrap" style={FIELD}>
      {showSource && (
        <Select
          size="xs"
          aria-label={`${label}: origem do valor`}
          style={{ flex: '1 1 90px', minWidth: 0 }}
          data={sources}
          value={value.source}
          allowDeselect={false}
          onChange={(source) => source && onChange({ source, number: source === NUMBER_SOURCE ? value.number : '' })}
        />
      )}
      {value.source === NUMBER_SOURCE && (
        <NumberInput
          size="xs"
          aria-label={`${label}: valor`}
          style={{ flex: '1 1 70px', minWidth: 0 }}
          min={bounds?.min}
          max={bounds?.max}
          decimalScale={1}
          decimalSeparator=","
          value={value.number}
          onChange={(raw) => onChange({ source: NUMBER_SOURCE, number: toDraftNumber(raw) })}
        />
      )}
    </Group>
  );
}

/**
 * Uma condição: variável, escopo (quando há "série anterior"), operador e
 * valor. Todas as opções e rótulos vêm do vocabulário do dono.
 */
export function ConditionLeaf({
  leaf,
  canDuplicate,
  onChange,
  onDuplicate,
  onRemove,
}: {
  leaf: LeafDraft;
  canDuplicate: boolean;
  onChange: (leaf: LeafDraft) => void;
  onDuplicate: () => void;
  onRemove: () => void;
}) {
  const scopes = scopeOptionsFor(leaf.variable);
  return (
    <Box data-condition-leaf>
      <Group gap="xs" wrap="wrap" align="flex-end">
        <Select
          size="xs"
          aria-label="Variável"
          style={FIELD}
          data={VARIABLE_OPTIONS}
          value={leaf.variable}
          allowDeselect={false}
          onChange={(variable) => variable && onChange(leafWithVariable(leaf, variable))}
        />
        {scopes.length > 1 && (
          <Select
            size="xs"
            aria-label="Escopo"
            style={FIELD}
            data={scopes}
            value={leaf.scope}
            allowDeselect={false}
            onChange={(scope) => scope && onChange(leafWithScope(leaf, scope))}
          />
        )}
        <Select
          size="xs"
          aria-label="Operador"
          style={{ flex: '1 1 100px', minWidth: 0 }}
          data={opOptionsFor(leaf.variable)}
          value={leaf.op}
          allowDeselect={false}
          onChange={(op) => op && onChange({ ...leaf, op })}
        />
        <ValueEditor
          label="Valor"
          variable={leaf.variable}
          value={leaf.value}
          onChange={(value) => onChange({ ...leaf, value })}
        />
        {isBetween(leaf.op) && (
          <>
            <Text size="xs" c={BRAND.textMuted}>
              e
            </Text>
            <ValueEditor
              label="Valor final"
              variable={leaf.variable}
              value={leaf.valueTo}
              onChange={(valueTo) => onChange({ ...leaf, valueTo })}
            />
          </>
        )}
        <Group gap={2} wrap="nowrap">
          <Tooltip label="Duplicar condição" withArrow>
            <ActionIcon variant="subtle" size="sm" onClick={onDuplicate} disabled={!canDuplicate} aria-label="Duplicar condição">
              <IconCopy size={14} />
            </ActionIcon>
          </Tooltip>
          <Tooltip label="Remover condição" withArrow>
            <ActionIcon variant="subtle" color="red" size="sm" onClick={onRemove} aria-label="Remover condição">
              <IconTrash size={14} />
            </ActionIcon>
          </Tooltip>
        </Group>
      </Group>
      {supportsRampCap(leaf.variable, leaf.scope) && (
        <Checkbox
          mt={4}
          size="xs"
          label="Limitar pelo RPE planejado da série (rampa da ficha)"
          checked={leaf.rampCap}
          onChange={(event) => onChange({ ...leaf, rampCap: event.currentTarget.checked })}
        />
      )}
    </Box>
  );
}
