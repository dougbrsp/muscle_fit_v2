'use client';

import { Button, Group, Table, Text } from '@mantine/core';
import { IconEye, IconPencil, IconTrash } from '@tabler/icons-react';
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
};

/** Versões do plano do aluno: rascunhos, a vigente e o histórico substituído. */
export function PlanHistory({ plans, selectedId, onSelect, onEdit, onDelete, deletingId }: Props) {
  if (plans.length === 0) {
    return (
      <Text c={BRAND.textMuted} size="sm">
        Nenhum plano para este aluno ainda. Crie um rascunho.
      </Text>
    );
  }
  return (
    <Table.ScrollContainer minWidth={560}>
      <Table verticalSpacing="xs" highlightOnHover>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Estado</Table.Th>
            <Table.Th>Estratégia</Table.Th>
            <Table.Th>Vigência</Table.Th>
            <Table.Th>Criado em</Table.Th>
            <Table.Th />
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {plans.map((plan) => {
            const isDraft = plan.state === 'draft';
            return (
              <Table.Tr key={plan.id} bg={plan.id === selectedId ? BRAND.bgSurface : undefined}>
                <Table.Td>
                  <PlanStateBadge state={plan.state} />
                </Table.Td>
                <Table.Td c={BRAND.textPrimary}>{nutritionLabel(STRATEGY_OPTIONS, plan.strategy)}</Table.Td>
                <Table.Td>
                  <Text size="sm">
                    {plan.effective_from
                      ? `${formatDateBR(plan.effective_from)}${plan.superseded_at ? ` — ${formatDateBR(plan.superseded_at)}` : ''}`
                      : '-'}
                  </Text>
                </Table.Td>
                <Table.Td>
                  <Text size="sm">{formatDateBR(plan.created_at)}</Text>
                </Table.Td>
                <Table.Td>
                  <Group gap={4} justify="flex-end" wrap="nowrap">
                    <Button size="xs" variant="subtle" leftSection={<IconEye size={12} />} onClick={() => onSelect(plan)}>
                      Ver
                    </Button>
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
                </Table.Td>
              </Table.Tr>
            );
          })}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}
