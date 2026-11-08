'use client';

import { ActionIcon, Badge, Box, Button, Group, SegmentedControl, Stack, Text, Tooltip } from '@mantine/core';
import { IconCopy, IconPlus, IconTrash } from '@tabler/icons-react';
import {
  LOGIC_OPTIONS,
  RULE_LIMITS,
  addGroupChild,
  addLeafChild,
  duplicateChild,
  optionLabel,
  setChild,
  type GroupDraft,
} from '@/lib/next-set-load-rules';
import { BRAND } from '@/theme';
import { ConditionLeaf } from './ConditionLeaf';

/**
 * Grupo de condições (E/OU) com recuo e parênteses visuais. Os filhos são
 * folhas ou subgrupos; o limite de níveis e de folhas vem de `rule_limits`.
 */
export function ConditionGroup({
  group,
  depth,
  leafCount,
  onChange,
  onDuplicate,
  onRemove,
}: {
  group: GroupDraft;
  /** Nível deste grupo (a raiz é 1). */
  depth: number;
  /** Total de folhas da regra (limite por regra). */
  leafCount: number;
  onChange: (group: GroupDraft) => void;
  onDuplicate?: () => void;
  onRemove?: () => void;
}) {
  const isRoot = depth === 1;
  const canAddLeaf = leafCount < RULE_LIMITS.max_leaves_per_rule;
  const canAddGroup = canAddLeaf && depth + 1 <= RULE_LIMITS.max_depth;
  const logicLabel = optionLabel(LOGIC_OPTIONS, group.logic);

  return (
    <Box
      data-condition-group
      pl={isRoot ? 0 : 'xs'}
      style={isRoot ? undefined : { borderLeft: `3px solid ${BRAND.textMuted}`, borderRadius: 2 }}
    >
      <Stack gap="xs">
        <Group gap="xs" wrap="wrap" justify="space-between">
          <Group gap="xs" wrap="wrap">
            {!isRoot && (
              <Text fw={700} size="lg" aria-hidden>
                (
              </Text>
            )}
            <SegmentedControl
              size="xs"
              aria-label="Juntar as condições com"
              value={group.logic}
              data={LOGIC_OPTIONS}
              onChange={(logic) => onChange({ ...group, logic })}
            />
          </Group>
          {!isRoot && (
            <Group gap={2} wrap="nowrap">
              <Tooltip label="Duplicar grupo" withArrow>
                <ActionIcon
                  variant="subtle"
                  size="sm"
                  onClick={onDuplicate}
                  disabled={!canAddGroup || !onDuplicate}
                  aria-label="Duplicar grupo"
                >
                  <IconCopy size={14} />
                </ActionIcon>
              </Tooltip>
              <Tooltip label="Remover grupo" withArrow>
                <ActionIcon variant="subtle" color="red" size="sm" onClick={onRemove} aria-label="Remover grupo">
                  <IconTrash size={14} />
                </ActionIcon>
              </Tooltip>
            </Group>
          )}
        </Group>

        {group.children.length === 0 && (
          <Text size="xs" c="red">
            Grupo vazio: adicione uma condição ou remova o grupo.
          </Text>
        )}

        {group.children.map((child, index) => (
          <Stack key={child.id} gap={4}>
            {index > 0 && (
              <Badge size="xs" variant="light" color="gray" style={{ alignSelf: 'flex-start' }}>
                {logicLabel}
              </Badge>
            )}
            {child.kind === 'leaf' ? (
              <ConditionLeaf
                leaf={child}
                canDuplicate={canAddLeaf}
                onChange={(next) => onChange(setChild(group, index, next))}
                onDuplicate={() => onChange(duplicateChild(group, index))}
                onRemove={() => onChange(setChild(group, index, null))}
              />
            ) : (
              <ConditionGroup
                group={child}
                depth={depth + 1}
                leafCount={leafCount}
                onChange={(next) => onChange(setChild(group, index, next))}
                onDuplicate={() => onChange(duplicateChild(group, index))}
                onRemove={() => onChange(setChild(group, index, null))}
              />
            )}
          </Stack>
        ))}

        <Group gap="xs" wrap="wrap">
          <Button
            size="compact-xs"
            variant="light"
            color="brand"
            leftSection={<IconPlus size={12} />}
            disabled={!canAddLeaf}
            onClick={() => onChange(addLeafChild(group))}
          >
            Adicionar condição
          </Button>
          <Button
            size="compact-xs"
            variant="light"
            color="gray"
            leftSection={<IconPlus size={12} />}
            disabled={!canAddGroup}
            onClick={() => onChange(addGroupChild(group))}
          >
            Adicionar grupo (parênteses)
          </Button>
        </Group>
        {!isRoot && (
          <Text fw={700} size="lg" aria-hidden>
            )
          </Text>
        )}
      </Stack>
    </Box>
  );
}
