'use client';

import { ActionIcon, Badge, Group, NumberInput, Paper, Select, Stack, Text, Tooltip } from '@mantine/core';
import { IconCopy, IconTrash } from '@tabler/icons-react';
import type { ReactNode } from 'react';
import {
  ACTION_OPTIONS,
  MAGNITUDE_KIND_OPTIONS,
  actionHasMagnitude,
  countLeaves,
  magnitudeBounds,
  magnitudeMode,
  ruleWithAction,
  toDraftNumber,
  type RuleDraft,
} from '@/lib/next-set-load-rules';
import { BRAND } from '@/theme';
import { ConditionGroup } from './ConditionGroup';
import { FormulaPreview } from './FormulaPreview';

/** Uma regra: bloco SE (árvore de condições), bloco ENTÃO (ação e magnitude) e o texto do servidor. */
export function RuleCard({
  rule,
  index,
  text,
  dragHandle,
  canDuplicate,
  onPatch,
  onDuplicate,
  onRemove,
}: {
  rule: RuleDraft;
  index: number;
  /** Texto do servidor (null = ainda gerando). */
  text: string | null;
  dragHandle?: ReactNode;
  canDuplicate: boolean;
  onPatch: (patch: Partial<RuleDraft>) => void;
  onDuplicate: () => void;
  onRemove: () => void;
}) {
  const bounds = magnitudeBounds(rule.magnitude.kind);
  // Recomendação do motor aceita magnitude com sinal (± n passos / ± p %); o dono valida o módulo.
  const signed = magnitudeMode(rule.action) === 'optional';
  return (
    <Paper withBorder radius="md" p="sm" data-rule-card>
      <Stack gap="sm">
        <Group justify="space-between" wrap="nowrap">
          <Group gap="xs" wrap="nowrap">
            {dragHandle}
            <Badge color="brand" variant="light" style={{ textTransform: 'none' }}>
              Regra {index + 1}
            </Badge>
          </Group>
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
        </Group>

        <Stack gap={4}>
          <Text size="xs" fw={700} c={BRAND.textMuted}>
            SE
          </Text>
          <ConditionGroup
            group={rule.when}
            depth={1}
            leafCount={countLeaves(rule.when)}
            onChange={(when) => onPatch({ when })}
          />
        </Stack>

        <Stack gap={4}>
          <Text size="xs" fw={700} c={BRAND.textMuted}>
            ENTÃO
          </Text>
          <Group gap="xs" wrap="wrap" align="flex-end">
            <Select
              size="xs"
              aria-label="Ação"
              style={{ flex: '1 1 150px', minWidth: 0 }}
              data={ACTION_OPTIONS}
              value={rule.action}
              allowDeselect={false}
              onChange={(action) => action && onPatch(ruleWithAction(rule, action))}
            />
            {actionHasMagnitude(rule.action) && (
              <>
                <NumberInput
                  size="xs"
                  aria-label="Magnitude"
                  style={{ flex: '1 1 70px', minWidth: 0 }}
                  min={signed ? -bounds.max : bounds.min}
                  allowNegative={signed}
                  max={bounds.max}
                  decimalScale={bounds.integer ? 0 : 1}
                  decimalSeparator=","
                  value={rule.magnitude.value}
                  onChange={(raw) => onPatch({ magnitude: { ...rule.magnitude, value: toDraftNumber(raw) } })}
                />
                <Select
                  size="xs"
                  aria-label="Tipo de magnitude"
                  style={{ flex: '1 1 150px', minWidth: 0 }}
                  data={MAGNITUDE_KIND_OPTIONS}
                  value={rule.magnitude.kind}
                  allowDeselect={false}
                  onChange={(kind) => {
                    if (!kind) return;
                    // Troca de tipo: volta ao mínimo do novo tipo (os limites são outros).
                    onPatch({ magnitude: { kind, value: magnitudeBounds(kind).min } });
                  }}
                />
              </>
            )}
          </Group>
        </Stack>

        <FormulaPreview text={text} />
      </Stack>
    </Paper>
  );
}
