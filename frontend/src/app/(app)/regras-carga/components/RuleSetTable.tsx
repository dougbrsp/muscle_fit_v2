'use client';

import { ActionIcon, Button, Checkbox, Group, NumberInput, Select, Table, Text, Tooltip } from '@mantine/core';
import { DragDropContext, Draggable, Droppable, type DropResult } from '@hello-pangea/dnd';
import { IconCopy, IconGripVertical, IconPlus, IconTrash } from '@tabler/icons-react';
import {
  ACTION_OPTIONS,
  FATIGUE_OPTIONS,
  MAGNITUDE_KIND_OPTIONS,
  REPS_VALUE_BOUNDS,
  REPS_VALUE_SOURCE_OPTIONS,
  RPE_VALUE_BOUNDS,
  RPE_VALUE_SOURCE_OPTIONS,
  RULE_LIMITS,
  SET_POSITION_OPTIONS,
  actionHasMagnitude,
  addRule,
  duplicateRule,
  magnitudeBounds,
  removeRule,
  reorderRules,
  toDraftNumber,
  updateRule,
  type RuleDraft,
} from '@/lib/next-set-load-rules';
import { BRAND } from '@/theme';
import { ConditionField } from './ConditionField';

const NOWRAP = { whiteSpace: 'nowrap' } as const;

function RuleRowCells({
  rule,
  index,
  text,
  onPatch,
  onDuplicate,
  onRemove,
  canDuplicate,
}: {
  rule: RuleDraft;
  index: number;
  text: string | null;
  onPatch: (patch: Partial<RuleDraft>) => void;
  onDuplicate: () => void;
  onRemove: () => void;
  canDuplicate: boolean;
}) {
  const bounds = magnitudeBounds(rule.magnitude.kind);
  return (
    <>
      <Table.Td>
        <Text size="sm" fw={600}>
          {index + 1}
        </Text>
      </Table.Td>
      <Table.Td>
        <Select
          size="xs"
          w={150}
          aria-label="Série"
          data={SET_POSITION_OPTIONS}
          value={rule.set_position}
          allowDeselect={false}
          onChange={(value) => value && onPatch({ set_position: value })}
        />
      </Table.Td>
      <Table.Td>
        <Select
          size="xs"
          w={140}
          aria-label="Fadiga"
          data={FATIGUE_OPTIONS}
          value={rule.fatigue}
          allowDeselect={false}
          onChange={(value) => value && onPatch({ fatigue: value })}
        />
      </Table.Td>
      <Table.Td>
        <ConditionField
          label="Reps"
          condition={rule.reps}
          sourceOptions={REPS_VALUE_SOURCE_OPTIONS}
          bounds={REPS_VALUE_BOUNDS}
          onChange={(reps) => onPatch({ reps })}
        />
      </Table.Td>
      <Table.Td>
        <ConditionField
          label="RPE"
          condition={rule.rpe}
          sourceOptions={RPE_VALUE_SOURCE_OPTIONS}
          bounds={RPE_VALUE_BOUNDS}
          decimal
          onChange={(rpe) => onPatch({ rpe })}
        />
      </Table.Td>
      <Table.Td>
        <Tooltip
          label="Com rampa de RPE na ficha, o limiar de RPE desta regra vira o RPE planejado da série quando ele for menor."
          multiline
          w={260}
          withArrow
        >
          <Checkbox
            aria-label="Respeita rampa planejada"
            checked={rule.respect_planned_ramp}
            onChange={(event) => onPatch({ respect_planned_ramp: event.currentTarget.checked })}
          />
        </Tooltip>
      </Table.Td>
      <Table.Td>
        <Select
          size="xs"
          w={150}
          aria-label="Ação"
          data={ACTION_OPTIONS}
          value={rule.action}
          allowDeselect={false}
          onChange={(value) => value && onPatch({ action: value })}
        />
      </Table.Td>
      <Table.Td>
        {actionHasMagnitude(rule.action) ? (
          <Group gap={4} wrap="nowrap">
            <NumberInput
              size="xs"
              w={70}
              aria-label="Magnitude"
              min={bounds.min}
              max={bounds.max}
              decimalScale={bounds.integer ? 0 : 1}
              decimalSeparator=","
              value={rule.magnitude.value}
              onChange={(raw) => onPatch({ magnitude: { ...rule.magnitude, value: toDraftNumber(raw) } })}
            />
            <Select
              size="xs"
              w={180}
              aria-label="Tipo de magnitude"
              data={MAGNITUDE_KIND_OPTIONS}
              value={rule.magnitude.kind}
              allowDeselect={false}
              onChange={(kind) => {
                if (!kind) return;
                // Troca de tipo: volta ao mínimo do novo tipo (os limites são outros).
                onPatch({ magnitude: { kind, value: magnitudeBounds(kind).min } });
              }}
            />
          </Group>
        ) : (
          <Text size="xs" c={BRAND.textMuted}>
            —
          </Text>
        )}
      </Table.Td>
      <Table.Td miw={260}>
        <Text size="xs" c={text ? undefined : BRAND.textMuted} data-rule-text>
          {text ?? 'Gerando o texto da regra…'}
        </Text>
      </Table.Td>
      <Table.Td>
        <Group gap={2} wrap="nowrap">
          <Tooltip label="Duplicar regra" withArrow>
            <ActionIcon variant="subtle" size="sm" onClick={onDuplicate} disabled={!canDuplicate} aria-label="Duplicar regra">
              <IconCopy size={14} />
            </ActionIcon>
          </Tooltip>
          <Tooltip label="Remover regra" withArrow>
            <ActionIcon variant="subtle" color="red" size="sm" onClick={onRemove} aria-label="Remover regra">
              <IconTrash size={14} />
            </ActionIcon>
          </Tooltip>
        </Group>
      </Table.Td>
    </>
  );
}

/**
 * Regras em ordem (arrastar para reordenar): a primeira que casa vence. Cada
 * linha só monta os campos com o vocabulário do dono; o texto vem do servidor.
 */
export function RuleSetTable({
  rules,
  texts,
  onChange,
}: {
  rules: RuleDraft[];
  /** Texto do servidor por regra, na ordem (null = ainda gerando). */
  texts: (string | null)[];
  onChange: (rules: RuleDraft[]) => void;
}) {
  const canAdd = rules.length < RULE_LIMITS.max_rules;

  function handleDragEnd(result: DropResult) {
    if (!result.destination) return;
    onChange(reorderRules(rules, result.source.index, result.destination.index));
  }

  return (
    <>
      <Table.ScrollContainer minWidth={1500}>
        <Table striped withTableBorder verticalSpacing="xs">
          <Table.Thead>
            <Table.Tr>
              <Table.Th w={36} />
              <Table.Th w={36} style={NOWRAP}>
                #
              </Table.Th>
              <Table.Th style={NOWRAP}>Série</Table.Th>
              <Table.Th style={NOWRAP}>Fadiga</Table.Th>
              <Table.Th style={NOWRAP}>Reps</Table.Th>
              <Table.Th style={NOWRAP}>RPE</Table.Th>
              <Table.Th style={NOWRAP}>Rampa</Table.Th>
              <Table.Th style={NOWRAP}>Ação</Table.Th>
              <Table.Th style={NOWRAP}>Magnitude</Table.Th>
              <Table.Th>Texto da regra</Table.Th>
              <Table.Th w={70} />
            </Table.Tr>
          </Table.Thead>
          <DragDropContext onDragEnd={handleDragEnd}>
            <Droppable droppableId="next-set-load-rules">
              {(droppableProvided) => (
                <Table.Tbody ref={droppableProvided.innerRef} {...droppableProvided.droppableProps}>
                  {rules.map((rule, index) => (
                    <Draggable key={rule.localId} draggableId={rule.localId} index={index}>
                      {(draggableProvided) => (
                        <Table.Tr ref={draggableProvided.innerRef} {...draggableProvided.draggableProps}>
                          <Table.Td>
                            <ActionIcon
                              variant="subtle"
                              color="gray"
                              size="sm"
                              {...draggableProvided.dragHandleProps}
                              aria-label="Arrastar para reordenar"
                            >
                              <IconGripVertical size={14} />
                            </ActionIcon>
                          </Table.Td>
                          <RuleRowCells
                            rule={rule}
                            index={index}
                            text={texts[index] ?? null}
                            canDuplicate={canAdd}
                            onPatch={(patch) => onChange(updateRule(rules, rule.localId, patch))}
                            onDuplicate={() => onChange(duplicateRule(rules, rule.localId))}
                            onRemove={() => onChange(removeRule(rules, rule.localId))}
                          />
                        </Table.Tr>
                      )}
                    </Draggable>
                  ))}
                  {droppableProvided.placeholder}
                  {rules.length === 0 && (
                    <Table.Tr>
                      <Table.Td colSpan={11}>
                        <Text c={BRAND.textMuted} ta="center" size="sm">
                          Nenhuma regra: adicione ao menos uma.
                        </Text>
                      </Table.Td>
                    </Table.Tr>
                  )}
                </Table.Tbody>
              )}
            </Droppable>
          </DragDropContext>
        </Table>
      </Table.ScrollContainer>
      <Button
        variant="light"
        color="brand"
        size="xs"
        mt="sm"
        leftSection={<IconPlus size={14} />}
        onClick={() => onChange(addRule(rules))}
        disabled={!canAdd}
      >
        Adicionar regra
      </Button>
    </>
  );
}
