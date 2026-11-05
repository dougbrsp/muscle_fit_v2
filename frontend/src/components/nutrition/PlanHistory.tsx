'use client';

import { Badge, Button, Card, Group, Stack, Text } from '@mantine/core';
import { IconArchive, IconArchiveOff, IconEye, IconPencil, IconTrash } from '@tabler/icons-react';
import { PlanStateBadge } from './PlanSummary';
import { STRATEGY_OPTIONS, nutritionLabel } from '@/lib/nutrition-constants';
import type { NutritionPlan } from '@/lib/nutrition-types';
import { formatDateBR } from '@/lib/physical-indicators-types';
import { BRAND } from '@/theme';

type Props = {
  plans: NutritionPlan[];
  selectedId: number | null;
  onSelect: (plan: NutritionPlan) => void;
  onEdit: (plan: NutritionPlan) => void;
  onDelete: (plan: NutritionPlan) => void;
  deletingId: number | null;
  /** Arquivar/desarquivar (migração 105); ausentes = sem as ações. O backend diz quem pode (`can_archive`). */
  onArchive?: (plan: NutritionPlan) => void;
  onUnarchive?: (plan: NutritionPlan) => void;
  archivingId?: number | null;
};

/**
 * Versões do plano: rascunhos, a vigente e o histórico substituído. Um cartão por versão, com as ações
 * quebrando de linha (nada de tabela com rolagem lateral no celular).
 */
export function PlanHistory({
  plans,
  selectedId,
  onSelect,
  onEdit,
  onDelete,
  deletingId,
  onArchive,
  onUnarchive,
  archivingId = null,
}: Props) {
  if (plans.length === 0) {
    return (
      <Text c={BRAND.textMuted} size="sm">
        Nenhum plano ainda. Crie um rascunho.
      </Text>
    );
  }
  return (
    <Stack gap="xs">
      {plans.map((plan) => {
        const isDraft = plan.state === 'draft';
        const vigencia = plan.effective_from
          ? `${formatDateBR(plan.effective_from)}${plan.superseded_at ? ` — ${formatDateBR(plan.superseded_at)}` : ''}`
          : null;
        return (
          <Card
            key={plan.id}
            withBorder
            radius="md"
            p="sm"
            bg={plan.id === selectedId ? BRAND.bgSurface : undefined}
            data-archived={plan.archived ? 'true' : undefined}
            style={plan.archived ? { opacity: 0.6 } : undefined}
          >
            <Group justify="space-between" align="flex-start" gap="xs">
              <Stack gap={2}>
                <Group gap="xs">
                  <PlanStateBadge state={plan.state} />
                  {plan.archived && (
                    <Badge variant="outline" color="gray" data-testid="plan-archived-badge">
                      Arquivado
                    </Badge>
                  )}
                  <Text c={BRAND.textPrimary} fw={500}>
                    {nutritionLabel(STRATEGY_OPTIONS, plan.strategy)}
                  </Text>
                </Group>
                <Text size="xs" c="dimmed">
                  {vigencia ? `Vigência ${vigencia} · ` : ''}Criado em {formatDateBR(plan.created_at)}
                </Text>
              </Stack>
              <Group gap={4} wrap="wrap">
                <Button size="xs" variant="subtle" leftSection={<IconEye size={12} />} onClick={() => onSelect(plan)}>
                  Ver
                </Button>
                {onArchive && plan.can_archive && (
                  <Button
                    size="xs"
                    variant="subtle"
                    color="gray"
                    leftSection={<IconArchive size={12} />}
                    loading={archivingId === plan.id}
                    onClick={() => onArchive(plan)}
                  >
                    Arquivar
                  </Button>
                )}
                {onUnarchive && plan.archived && (
                  <Button
                    size="xs"
                    variant="subtle"
                    color="gray"
                    leftSection={<IconArchiveOff size={12} />}
                    loading={archivingId === plan.id}
                    onClick={() => onUnarchive(plan)}
                  >
                    Desarquivar
                  </Button>
                )}
                {isDraft && (
                  <>
                    <Button size="xs" variant="subtle" leftSection={<IconPencil size={12} />} onClick={() => onEdit(plan)}>
                      Editar
                    </Button>
                    <Button
                      size="xs"
                      variant="subtle"
                      color="red"
                      leftSection={<IconTrash size={12} />}
                      loading={deletingId === plan.id}
                      onClick={() => onDelete(plan)}
                    >
                      Excluir
                    </Button>
                  </>
                )}
              </Group>
            </Group>
          </Card>
        );
      })}
    </Stack>
  );
}
