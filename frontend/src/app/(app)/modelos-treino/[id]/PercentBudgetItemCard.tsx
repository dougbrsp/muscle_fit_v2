'use client';

import { ActionIcon, Badge, Box, Checkbox, Group, Input, Paper, Select, Stack, Text, Tooltip } from '@mantine/core';
import { IconTrash } from '@tabler/icons-react';
import type { ItemActivationSummary } from '@/lib/item-activation-summary';
import { effectiveItemGoal, itemFieldPlaceholders, type TrainingGoalsResponse } from '@/lib/training-goals';
import type { ItemState } from '@/lib/workout-plan-template-form';
import {
  ITEM_KIND_OPTIONS,
  type PriorFatigue,
  type TemplateSafetyWarning,
  type WorkoutPlanTemplateItemKind,
  type WorkoutPlanTemplateSlotType,
} from '@/lib/workout-plan-templates-types';
import { BRAND } from '@/theme';
import { ItemActivationLine } from '../ItemActivationLine';
import { BlurCommitNumberInput, BlurCommitTextInput } from './BlurCommitInputs';
import { cardioShortDescribe, newCardioPrescription } from '@/lib/cardio-prescription';
import { CardioPrescriptionEditor } from './CardioPrescriptionEditor';
import { FIELD_GRID_STYLE } from './fieldGridStyle';
import { ItemCollapsedSummary, ItemCollapseToggle, itemCardStyle, PriorFatigueStripe } from './ItemCollapsedSummary';
import { ItemGoalSelect } from './ItemGoalSelect';
import { ItemSafetyWarnings } from './ItemSafetyWarnings';
import { DragHandle, type SortableHandle } from './sortable';

// Card de item do modo volume_mode='percent_budget'. Antes era uma linha da
// tabela do slot (8 colunas, `minWidth=760`); com a grade de slots (até 4 por
// linha, ~280 px cada) virou card empilhado com os MESMOS campos e ações: os
// rótulos que ficavam no cabeçalho da tabela viraram labels dos campos.
// Ordem: alça (M9) + campos + linha de ativação (M11) + avisos (PR-12).
type PercentBudgetItemCardProps = {
  item: ItemState;
  // Posição 0-based no slot; vira a numeração 1-based.
  itemIndex: number;
  slotType: WorkoutPlanTemplateSlotType;
  trainingTypeId: string | null;
  // M97 F3: objetivos de `GET /training-goals` e o do Modelo (override no item).
  trainingGoals?: TrainingGoalsResponse;
  templateGoal?: string | null;
  muscleGroupOptions: { value: string; label: string }[];
  activation?: ItemActivationSummary;
  // Fadiga prévia do item no slot (preview): faixa de cor lateral.
  priorFatigue?: PriorFatigue | null;
  safetyWarnings: TemplateSafetyWarning[];
  dragHandle?: SortableHandle;
  muscleGroupNameById: Map<number, string>;
  // Card recolhido: resumo para organizar/arrastar (estado só do navegador).
  collapsed: boolean;
  onToggleCollapsed: () => void;
  onChangeItem: (patch: Partial<ItemState>) => void;
  onRemoveItem: () => void;
};

export function PercentBudgetItemCard({
  item,
  itemIndex,
  slotType,
  trainingTypeId,
  trainingGoals,
  templateGoal = null,
  muscleGroupOptions,
  activation,
  priorFatigue,
  safetyWarnings,
  dragHandle,
  muscleGroupNameById,
  collapsed,
  onToggleCollapsed,
  onChangeItem,
  onRemoveItem,
}: PercentBudgetItemCardProps) {
  // M97 F3: defaults do objetivo efetivo do item, prontos da API.
  const placeholders = itemFieldPlaceholders(effectiveItemGoal(trainingGoals, item.training_goal, templateGoal));
  const isMuscleGroup = item.item_kind === 'muscle_group';
  if (collapsed) {
    const groupName = item.muscle_group_id != null ? muscleGroupNameById.get(item.muscle_group_id) || null : null;
    const pctRange =
      item.priority_min_pct != null || item.priority_max_pct != null
        ? `% da meta: ${item.priority_min_pct ?? '—'}–${item.priority_max_pct ?? '—'}%`
        : null;
    // Este modo não tem equipamento: a linha 2 mostra a faixa % do item (ou a
    // prescrição, no cardio: resumo do dono `describe_short`, M98 C1).
    let detail: string;
    if (isMuscleGroup) detail = pctRange ?? 'Faixa % da meta não definida';
    else detail = cardioShortDescribe(item);
    return (
      <Paper withBorder radius="md" p="sm" style={itemCardStyle(itemIndex)}>
        <PriorFatigueStripe priorFatigue={priorFatigue} />
        <ItemCollapsedSummary
          itemIndex={itemIndex}
          dragHandle={dragHandle}
          title={isMuscleGroup ? groupName || 'Grupo muscular não escolhido' : 'Cardio'}
          detail={detail}
          detailMuted={isMuscleGroup && pctRange == null}
          activation={activation}
          onExpand={onToggleCollapsed}
        />
      </Paper>
    );
  }
  return (
    <Paper withBorder radius="md" p="sm" style={itemCardStyle(itemIndex)}>
      <PriorFatigueStripe priorFatigue={priorFatigue} />
      <Stack gap="xs">
        {/* Linha 1: número, alça, tipo e lixeira. */}
        <Group gap={4} wrap="nowrap" align="flex-end">
          <Badge
            size="sm"
            circle
            variant="light"
            color="gray"
            aria-label={`Exercício número ${itemIndex + 1} do slot`}
            mb={4}
          >
            {itemIndex + 1}
          </Badge>
          <Box mb={2}>
            <DragHandle handle={dragHandle} label="Arrastar para reordenar" />
          </Box>
          <Select
            size="xs"
            label="Tipo"
            data={ITEM_KIND_OPTIONS}
            value={item.item_kind}
            onChange={(v) => {
              const kind = (v as WorkoutPlanTemplateItemKind) || 'muscle_group';
              // M98 C1: virou cardio agora -> a prescrição padrão do dono.
              onChangeItem({
                item_kind: kind,
                ...(kind === 'cardio' && item.item_kind !== 'cardio' ? newCardioPrescription() : {}),
              });
            }}
            disabled={slotType === 'descanso'}
            style={{ flex: 1, minWidth: 0 }}
          />
          <Box mb={4}>
            <ItemCollapseToggle collapsed={false} onToggle={onToggleCollapsed} />
          </Box>
          <ActionIcon
            size="sm"
            color="red"
            variant="subtle"
            onClick={onRemoveItem}
            aria-label="Remover item"
            mb={4}
          >
            <IconTrash size={14} />
          </ActionIcon>
        </Group>

        {/* Linha 2: grupo muscular ou a prescrição do cardio (Normal / HIIT, M98 C1). */}
        {isMuscleGroup ? (
          <Select
            size="xs"
            label="Grupo muscular"
            placeholder="Selecione..."
            searchable
            data={muscleGroupOptions}
            value={item.muscle_group_id != null ? String(item.muscle_group_id) : null}
            onChange={(v) => onChangeItem({ muscle_group_id: v ? Number(v) : null })}
            w="100%"
          />
        ) : (
          <CardioPrescriptionEditor item={item} onUpdate={onChangeItem} />
        )}

        {/* Linha 3: séries, reps e RPE (só grupo muscular). */}
        {isMuscleGroup && (
          <Box style={FIELD_GRID_STYLE}>
            <ItemGoalSelect
              trainingGoals={trainingGoals}
              templateGoal={templateGoal}
              value={item.training_goal}
              onChange={(training_goal) => onChangeItem({ training_goal })}
            />
            {trainingTypeId != null ? (
              <Input.Wrapper size="xs" label="Séries">
                <Tooltip
                  label="O motor v2 ignora este valor quando o modelo tem Meta de Ativação associada — o volume vem do orçamento (% meta min-máx) da meta de ativação."
                  multiline
                  w={220}
                >
                  <Text size="xs" c={BRAND.textMuted} style={{ cursor: 'help' }} py={6}>
                    —
                  </Text>
                </Tooltip>
              </Input.Wrapper>
            ) : (
              <BlurCommitNumberInput
                size="xs"
                label="Séries"
                value={item.sets_count ?? undefined}
                onCommit={(v) => onChangeItem({ sets_count: typeof v === 'number' ? v : null })}
                min={0}
              />
            )}
            <BlurCommitTextInput
              size="xs"
              label="Reps"
              placeholder={placeholders?.reps ?? 'ex.: 8-12'}
              value={item.reps_target ?? ''}
              onCommit={(v) => onChangeItem({ reps_target: v || null })}
            />
            <BlurCommitTextInput
              size="xs"
              label="RPE"
              placeholder={placeholders?.rpe ?? 'ex.: 7-8'}
              value={item.rpe_target ?? ''}
              onCommit={(v) => onChangeItem({ rpe_target: v || null })}
            />
          </Box>
        )}

        {/* Linha 4: priorizado e % da meta (min-máx, só grupo muscular). */}
        <Group gap="xs" wrap="wrap" align="flex-end">
          <Checkbox
            size="xs"
            label="Priorizado"
            checked={item.prioritize}
            onChange={(e) => onChangeItem({ prioritize: e.currentTarget.checked })}
            mb={6}
          />
          {isMuscleGroup && (
            <Group gap={4} wrap="nowrap" align="flex-end">
              <BlurCommitNumberInput
                size="xs"
                label={
                  <Tooltip label="Faixa % da meta semanal do grupo a entregar neste slot (motor v2, opcional)">
                    <span>% meta mín.</span>
                  </Tooltip>
                }
                placeholder="min"
                value={item.priority_min_pct ?? undefined}
                onCommit={(v) =>
                  onChangeItem({
                    priority_min_pct: typeof v === 'number' ? v : null,
                  })
                }
                min={0}
                max={100}
                w={80}
              />
              <BlurCommitNumberInput
                size="xs"
                label="% meta máx."
                placeholder="máx"
                value={item.priority_max_pct ?? undefined}
                onCommit={(v) =>
                  onChangeItem({
                    priority_max_pct: typeof v === 'number' ? v : null,
                  })
                }
                min={0}
                max={100}
                w={80}
              />
            </Group>
          )}
        </Group>
      </Stack>

      {activation && <ItemActivationLine summary={activation} />}
      <ItemSafetyWarnings warnings={safetyWarnings} />
    </Paper>
  );
}
