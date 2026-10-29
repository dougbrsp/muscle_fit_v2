'use client';

import { Badge, Button, Collapse, Group, Paper, Stack, Text } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { IconChevronDown, IconChevronUp, IconHistory } from '@tabler/icons-react';
import {
  EXERCISE_CARD_STATUS_BADGES,
  formatCardDateTime,
  formatCostUsd,
  generationFailureMessage,
  generationLabel,
  rejectReasonLabel,
  totalCostUsd,
} from '@/lib/exercise-cards';
import type { ExerciseCardGeneration } from '@/lib/exercise-cards-types';
import { BRAND } from '@/theme';

function HistoryRow({ item }: { item: ExerciseCardGeneration }) {
  const badge = EXERCISE_CARD_STATUS_BADGES[item.status];
  const cost = formatCostUsd(item.cost_usd);
  const when = formatCardDateTime(item.created_at);
  const reason = rejectReasonLabel(item.reject_reason);
  const meta = [when, item.created_by != null ? `por usuário #${item.created_by}` : null, cost].filter(Boolean);

  return (
    <Paper withBorder p="xs" radius="md" bg={BRAND.bgSurface} data-card-generation={item.id} data-status={item.status}>
      <Stack gap={4}>
        <Group gap="xs" wrap="wrap">
          <Text fw={700} size="sm" c={BRAND.textPrimary}>
            {generationLabel(item)}
          </Text>
          <Badge color={badge.color} variant="light" size="sm">
            {badge.label}
          </Badge>
          {item.parent_generation_id != null && (
            <Text size="xs" c={BRAND.textMuted}>
              a partir de #{item.parent_generation_id}
            </Text>
          )}
        </Group>
        {meta.length > 0 && (
          <Text size="xs" c={BRAND.textMuted}>
            {meta.join(' · ')}
          </Text>
        )}
        {item.approved_by != null && item.published_at && (
          <Text size="xs" c={BRAND.textMuted}>
            Publicado em {formatCardDateTime(item.published_at)} por usuário #{item.approved_by}
          </Text>
        )}
        {item.request_instruction && (
          <Text size="xs" c={BRAND.textMuted}>
            Instrução: “{item.request_instruction}”
          </Text>
        )}
        {reason && (
          <Text size="xs" c={BRAND.textMuted}>
            Motivo: {reason}
          </Text>
        )}
        {item.status === 'FAILED' && (
          <Text size="xs" c="red.4">
            {generationFailureMessage(item.error_code)}
          </Text>
        )}
      </Stack>
    </Paper>
  );
}

/**
 * Histórico das gerações do card (mais recente primeiro, até 20 — a listagem
 * do backend): versão, status, quem, quando, instrução, motivo e custo.
 * Colapsado por padrão para não empurrar o resto do drawer.
 */
export function ExerciseCardHistoryList({
  items,
  defaultOpened = false,
}: {
  items: readonly ExerciseCardGeneration[];
  defaultOpened?: boolean;
}) {
  const [opened, { toggle }] = useDisclosure(defaultOpened);
  if (items.length === 0) return null;
  const total = formatCostUsd(totalCostUsd(items));

  return (
    <Stack gap="xs" data-card-history>
      <Group justify="space-between" wrap="wrap">
        <Button
          variant="subtle"
          size="xs"
          color="gray"
          leftSection={<IconHistory size={14} />}
          rightSection={opened ? <IconChevronUp size={14} /> : <IconChevronDown size={14} />}
          onClick={toggle}
          aria-expanded={opened}
        >
          Histórico de gerações ({items.length})
        </Button>
        {total && (
          <Text size="xs" c={BRAND.textMuted}>
            Custo total: {total}
          </Text>
        )}
      </Group>
      <Collapse in={opened}>
        <Stack gap="xs">
          {items.map((item) => (
            <HistoryRow key={item.id} item={item} />
          ))}
        </Stack>
      </Collapse>
    </Stack>
  );
}
