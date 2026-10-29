'use client';

import { ActionIcon, Alert, Badge, Button, Group, Paper, Select, Stack, Text, Tooltip } from '@mantine/core';
import { SortableContext, useSortable, verticalListSortingStrategy } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import {
  IconAlertTriangle,
  IconArrowDown,
  IconArrowUp,
  IconChevronDown,
  IconChevronUp,
  IconChevronsDown,
  IconChevronsUp,
  IconClock,
  IconInfoCircle,
  IconPlus,
  IconTrash,
} from '@tabler/icons-react';
import { useMemo, type CSSProperties } from 'react';
import { itemActivationKey, type ItemActivationSummary } from '@/lib/item-activation-summary';
import { allItemsCollapsed } from '@/lib/item-collapse';
import type { MethodologyItemDirectShare, MethodologyVolumeEvaluation } from '@/lib/methodology-volume-types';
import { collapsedSlotTotalSeconds, formatRestSeconds } from '@/lib/template-time-estimate';
import { supportsBatchPct, type BatchPctDraft } from '@/lib/slot-batch-pct';
import { collapsedSlotTitle } from '@/lib/weekday-label';
import {
  formatExerciseCount,
  slotGroupBudgets,
  slotGroupSets,
  type MuscleGroupParentMap,
  type SlotGroupSummaryLine,
} from '@/lib/slot-group-sets';
import { slotGroupSetsWithStatus, type SlotGroupPlanningStatus } from '@/lib/slot-group-status';
import {
  applySlotGoal,
  goalBadgeColor,
  itemAcceptsGoal,
  itemGoalFromSelect,
  itemGoalSelectData,
  itemGoalSelectValue,
  slotCommonGoal,
  slotEffectiveGoals,
  type TrainingGoalsResponse,
} from '@/lib/training-goals';
import {
  countSlotSafetyWarnings,
  isNormalSlotWithoutItems,
  safetyWarningsForItem,
  type ItemState,
  type SlotState,
} from '@/lib/workout-plan-template-form';
import {
  SLOT_TYPE_OPTIONS,
  WEEKDAY_OPTIONS,
  type PreviewActivationItemBudget,
  type PriorFatigue,
  type TemplateSafetyWarning,
  type VolumeMode,
  type WorkoutPlanTemplateItemKind,
  type WorkoutPlanTemplateSlotType,
} from '@/lib/workout-plan-templates-types';
import { BRAND } from '@/theme';
import { BlurCommitTextInput } from './BlurCommitInputs';
import { EquipmentSeriesItemCard } from './EquipmentSeriesItemCard';
import { PercentBudgetItemCard } from './PercentBudgetItemCard';
import { SlotBatchPctShortcut } from './SlotBatchPctShortcut';
import { SlotTotalTime } from './SlotTotalTime';
import { DragHandle, SortableItem, type SortableData, type SortableHandle } from './sortable';

// Um slot do ciclo no builder de Modelo, numa célula da grade: barra de ações
// no topo (alça à esquerda; recolher, mover e remover à direita), nome em
// linha inteira abaixo (tipo e dia da semana quando expandido),
// atalho de % em lote, itens arrastáveis (cards nos dois modos) e botões de
// adicionar. O `DndContext` e o `SortableContext` dos SLOTS ficam no builder;
// aqui há o sortable do próprio slot (só pela alça) e o `SortableContext` dos
// itens deste slot.
type SlotEditorProps = {
  slot: SlotState;
  // Posição 0-based no ciclo; `slotIndex + 1` é o `sequence` do payload.
  slotIndex: number;
  slotCount: number;
  volumeMode: VolumeMode;
  trainingTypeId: string | null;
  // M97 F3: objetivos de `GET /training-goals` e o do Modelo (null = default).
  trainingGoals?: TrainingGoalsResponse;
  templateGoal?: string | null;
  // Todos os avisos de segurança do modelo — cada item filtra os seus.
  safetyWarnings: TemplateSafetyWarning[];
  itemActivationByKey: Map<string, ItemActivationSummary>;
  // Fadiga prévia por item (`item_prior_fatigue[]` do preview), mesma chave.
  itemPriorFatigueByKey?: Map<string, PriorFatigue>;
  // Lote 3: fatia de cada card por bloco (`item_shares` do preview), mesma chave.
  itemDirectShareByKey?: Map<string, MethodologyItemDirectShare>;
  // Modo 'percent_budget': `item_budgets` da query de preview compartilhada
  // (todos os slots; aqui filtra pelo `sequence` deste). Ausente enquanto
  // carrega ou sem tipo de treino.
  itemBudgets?: ReadonlyArray<PreviewActivationItemBudget>;
  methodologyEvaluation?: MethodologyVolumeEvaluation;
  methodologyPending: boolean;
  muscleGroupOptions: { value: string; label: string }[];
  muscleGroupNameById: Map<number, string>;
  /** `methodology_parent_group_id` por grupo: o resumo recolhido agrega no grupo principal. */
  muscleGroupParentById: MuscleGroupParentMap;
  expandedItemIds: Set<string>;
  // Slot recolhido: só o resumo do cabeçalho (estado só do navegador).
  collapsed: boolean;
  onToggleCollapsed: () => void;
  onToggleItemExpanded: (itemLocalId: string) => void;
  // Cards de item recolhidos (por `localId`, só navegador). Independente de
  // `expandedItemIds` (configurações avançadas).
  collapsedItemIds: ReadonlySet<string>;
  onToggleItemCollapsed: (itemLocalId: string) => void;
  onSetItemsCollapsed: (itemLocalIds: string[], collapsed: boolean) => void;
  onChangeSlot: (patch: Partial<SlotState>) => void;
  onMoveSlot: (direction: -1 | 1) => void;
  onRemoveSlot: () => void;
  onAddItem: (kind: WorkoutPlanTemplateItemKind) => void;
  onChangeItem: (itemLocalId: string, patch: Partial<ItemState>) => void;
  onRemoveItem: (itemLocalId: string) => void;
  onApplyBatchPct: (draft: BatchPctDraft) => void;
};

// Linha do resumo recolhido, por grupo PRINCIPAL: rótulo pronto
// ("14 séries/4 exercícios" no modo equipamento, "20–30% · ≈3–4 séries/2
// exercícios" no modo orçamento) + status (só modo equipamento).
type SlotGroupSummaryRow = SlotGroupSummaryLine & { planning?: SlotGroupPlanningStatus | null };

/**
 * Indicador de planejamento de um grupo no resumo recolhido: o MESMO ícone e
 * cor que o card de item mostra na linha "Séries diretas" (status combinado
 * do bloco no Modelo, domínio `methodologyVolume`). O ícone difere por estado
 * e o rótulo vai no tooltip e no `aria-label` — nunca só a cor.
 */
function GroupPlanningIndicator({ planning }: { planning: SlotGroupPlanningStatus }) {
  const { Icon } = planning.status;
  return (
    <Tooltip label={planning.title} multiline w={240} openDelay={200} withinPortal>
      <span
        role="img"
        aria-label={planning.ariaLabel}
        tabIndex={0}
        data-status={planning.statusCode}
        style={{ display: 'inline-flex', flexShrink: 0, cursor: 'help' }}
      >
        <Icon size={13} aria-hidden style={{ color: `var(--mantine-color-${planning.status.color}-6)` }} />
      </span>
    </Tooltip>
  );
}

/**
 * Resumo do slot recolhido: "Dia N · SEG · nome" (dia só se informado; linha inteira, quebra se
 * precisar), tipo (descanso), quantidade de exercícios e de itens com aviso
 * de segurança. Com `groupSets`, lista abaixo um grupo muscular por linha com
 * o rótulo pronto de cada uma (séries de trabalho no modo equipamento; faixa %
 * e séries projetadas no modo orçamento), com o indicador de planejamento do
 * grupo quando houver avaliação. Com `totalSeconds`, mostra ao lado da contagem o
 * "Tempo total de treino" (o mesmo valor do slot expandido). Também é a "fantasma" do `DragOverlay` ao
 * arrastar um slot (lá, sem a lista).
 */
export function SlotCollapsedSummary({
  slot,
  slotIndex,
  safetyWarningCount,
  groupSets,
  totalSeconds,
}: {
  slot: SlotState;
  slotIndex: number;
  safetyWarningCount: number;
  groupSets?: ReadonlyArray<SlotGroupSummaryRow>;
  totalSeconds?: number | null;
}) {
  return (
    <Stack gap={4} style={{ minWidth: 0 }}>
      <Text fw={600} size="sm" c={BRAND.textPrimary} style={{ overflowWrap: 'anywhere' }}>
        {collapsedSlotTitle(slotIndex, slot.name, slot.weekday)}
      </Text>
      <Group gap={4} wrap="wrap">
        {slot.slot_type === 'descanso' && (
          <Badge size="sm" color="gray" variant="light">
            Descanso
          </Badge>
        )}
        {isNormalSlotWithoutItems(slot) ? (
          // #318 no resumo recolhido: mesmo aviso, em forma de badge.
          <Tooltip label='Slot "Normal" sem exercícios conta como treino esperado na aderência — expanda para marcar como descanso.'>
            <Badge size="sm" color="yellow" variant="light" leftSection={<IconInfoCircle size={12} />}>
              Sem exercícios
            </Badge>
          </Tooltip>
        ) : (
          <Badge size="sm" color="brand" variant="light">
            {formatExerciseCount(slot.items.length)}
          </Badge>
        )}
        {totalSeconds != null && totalSeconds > 0 && (
          <Tooltip label="Tempo total de treino estimado">
            <Badge
              size="sm"
              color="brand"
              variant="light"
              leftSection={<IconClock size={12} aria-hidden />}
              aria-label={`Tempo total de treino estimado: ${formatRestSeconds(totalSeconds)}`}
            >
              {formatRestSeconds(totalSeconds)}
            </Badge>
          </Tooltip>
        )}
        {safetyWarningCount > 0 && (
          <Tooltip label="Há itens deste slot com aviso de segurança — expanda o slot para ver.">
            <Badge size="sm" color="red" variant="light" leftSection={<IconAlertTriangle size={12} />}>
              {safetyWarningCount}
            </Badge>
          </Tooltip>
        )}
      </Group>
      {groupSets && groupSets.length > 0 && (
        <Stack gap={0} aria-label="Séries por grupo muscular">
          {groupSets.map((row) => (
            <Group key={row.muscleGroupId} gap="xs" wrap="nowrap" justify="space-between" align="flex-start">
              <Group gap={4} wrap="nowrap" align="center" style={{ minWidth: 0 }}>
                <Text size="xs" c={BRAND.textPrimary} style={{ minWidth: 0, overflowWrap: 'anywhere' }}>
                  {row.name}
                </Text>
                {row.planning && <GroupPlanningIndicator planning={row.planning} />}
              </Group>
              <Text size="xs" c={BRAND.textMuted} style={{ flexShrink: 0, whiteSpace: 'nowrap' }}>
                {row.label}
              </Text>
            </Group>
          ))}
        </Stack>
      )}
    </Stack>
  );
}

/** Versão do slot exibida no `DragOverlay` enquanto ele é arrastado. */
export function SlotDragPreview(props: { slot: SlotState; slotIndex: number; safetyWarningCount: number }) {
  return (
    <Paper
      withBorder
      radius="md"
      p="sm"
      shadow="md"
      style={{ borderColor: BRAND.border, background: BRAND.bgElevated, cursor: 'grabbing' }}
    >
      <SlotCollapsedSummary {...props} />
    </Paper>
  );
}

export function SlotEditor({
  slot,
  slotIndex,
  slotCount,
  volumeMode,
  trainingTypeId,
  trainingGoals,
  templateGoal,
  safetyWarnings,
  itemActivationByKey,
  itemPriorFatigueByKey,
  itemDirectShareByKey,
  itemBudgets,
  methodologyEvaluation,
  methodologyPending,
  muscleGroupOptions,
  muscleGroupNameById,
  muscleGroupParentById,
  expandedItemIds,
  collapsed,
  onToggleCollapsed,
  onToggleItemExpanded,
  collapsedItemIds,
  onToggleItemCollapsed,
  onSetItemsCollapsed,
  onChangeSlot,
  onMoveSlot,
  onRemoveSlot,
  onAddItem,
  onChangeItem,
  onRemoveItem,
  onApplyBatchPct,
}: SlotEditorProps) {
  const sortableData: SortableData = { type: 'slot' };
  const { setNodeRef, setActivatorNodeRef, attributes, listeners, transform, transition, isDragging } = useSortable({
    id: slot.localId,
    data: sortableData,
  });
  const slotHandle: SortableHandle = { setActivatorNodeRef, attributes, listeners };
  // Translate (sem scale): slots da mesma linha têm alturas diferentes e o
  // scale do rectSortingStrategy distorceria o conteúdo.
  const style: CSSProperties = {
    transform: CSS.Translate.toString(transform),
    transition,
    opacity: isDragging ? 0.4 : undefined,
  };
  const safetyWarningCount = countSlotSafetyWarnings(safetyWarnings, slotIndex, slot);
  const itemIds = slot.items.map((item) => item.localId);
  const cardsCollapsed = allItemsCollapsed(collapsedItemIds, itemIds);
  const cardsToggleLabel = cardsCollapsed ? 'Expandir cards' : 'Recolher cards';
  // Só o resumo recolhido usa a lista; expandido, não calcula. Modo
  // equipamento: séries e exercícios por grupo principal + status (o mesmo
  // dado que os cards recebem, `methodologyEvaluation`). Modo orçamento: todos
  // os grupos principais com a faixa % e as séries projetadas (`item_budgets`
  // do preview), sem status. Subgrupos somam no principal nos dois modos.
  const groupSets = useMemo((): SlotGroupSummaryRow[] | undefined => {
    if (!collapsed) return undefined;
    if (volumeMode === 'percent_budget') {
      const slotSequence = slotIndex + 1;
      const slotBudgets = itemBudgets?.filter((budget) => budget.slot_sequence === slotSequence);
      return slotGroupBudgets(slot, muscleGroupNameById, slotBudgets, muscleGroupParentById);
    }
    return slotGroupSetsWithStatus({
      rows: slotGroupSets(slot, muscleGroupNameById, muscleGroupParentById),
      volumeMode,
      slotSequence: slotIndex + 1,
      evaluation: methodologyEvaluation,
    });
  }, [
    collapsed,
    slot,
    muscleGroupNameById,
    muscleGroupParentById,
    volumeMode,
    slotIndex,
    methodologyEvaluation,
    itemBudgets,
  ]);
  // Mesmo valor do `SlotTotalTime` do slot expandido; só calcula recolhido.
  const collapsedTotalSeconds = useMemo(
    () => (collapsed ? collapsedSlotTotalSeconds(slot, volumeMode) : null),
    [collapsed, slot, volumeMode],
  );

  return (
    <div ref={setNodeRef} style={style}>
      <Paper withBorder radius="md" p="sm" style={{ borderColor: BRAND.border }}>
        {/* Barra de ações no topo (nos dois estados): alça à esquerda; recolher,
            mover e remover à direita. Só a alça inicia o arraste do slot. */}
        <Group gap={4} wrap="nowrap" align="center" justify="space-between" mb={4}>
          <Group gap={4} wrap="nowrap" align="center" style={{ minWidth: 0 }}>
            <DragHandle handle={slotHandle} label={`Arrastar o dia ${slotIndex + 1} para outra posição da grade`} />
            {!collapsed && (
              <Badge color="brand" variant="light" style={{ flexShrink: 0 }}>
                Dia {slotIndex + 1}
              </Badge>
            )}
          </Group>
          <Group gap={4} wrap="nowrap" align="center" style={{ flexShrink: 0 }}>
            {/* Slot aberto com itens: recolhe/expande todos os cards dele de uma vez. */}
            {!collapsed && itemIds.length > 0 && (
              <Tooltip label={cardsToggleLabel} openDelay={400}>
                <ActionIcon
                  variant="subtle"
                  color="brand"
                  size="sm"
                  onClick={() => onSetItemsCollapsed(itemIds, !cardsCollapsed)}
                  aria-label={cardsToggleLabel}
                  aria-pressed={cardsCollapsed}
                >
                  {cardsCollapsed ? <IconChevronsDown size={14} /> : <IconChevronsUp size={14} />}
                </ActionIcon>
              </Tooltip>
            )}
            <Tooltip label={collapsed ? 'Expandir slot' : 'Recolher slot'} openDelay={400}>
              <ActionIcon
                variant="subtle"
                color="gray"
                size="sm"
                onClick={onToggleCollapsed}
                aria-label={collapsed ? 'Expandir slot' : 'Recolher slot'}
                aria-expanded={!collapsed}
              >
                {collapsed ? <IconChevronDown size={14} /> : <IconChevronUp size={14} />}
              </ActionIcon>
            </Tooltip>
            <ActionIcon
              variant="light"
              color="brand"
              size="sm"
              disabled={slotIndex === 0}
              onClick={() => onMoveSlot(-1)}
              aria-label="Mover para cima"
            >
              <IconArrowUp size={14} />
            </ActionIcon>
            <ActionIcon
              variant="light"
              color="brand"
              size="sm"
              disabled={slotIndex === slotCount - 1}
              onClick={() => onMoveSlot(1)}
              aria-label="Mover para baixo"
            >
              <IconArrowDown size={14} />
            </ActionIcon>
            <ActionIcon variant="light" color="red" size="sm" onClick={() => onRemoveSlot()} aria-label="Remover slot">
              <IconTrash size={14} />
            </ActionIcon>
          </Group>
        </Group>

        {/* Título em linha inteira: resumo (recolhido) ou nome editável. */}
        {collapsed ? (
          <SlotCollapsedSummary
            slot={slot}
            slotIndex={slotIndex}
            safetyWarningCount={safetyWarningCount}
            groupSets={groupSets}
            totalSeconds={collapsedTotalSeconds}
          />
        ) : (
          <BlurCommitTextInput
            size="xs"
            aria-label="Nome do slot"
            placeholder="Nome do slot"
            value={slot.name}
            onCommit={(v) => onChangeSlot({ name: v })}
            mb="xs"
          />
        )}

        {!collapsed && (
          <>
            {/* Linha 2: tipo e dia da semana (informativo). */}
            <Group gap="xs" wrap="wrap" align="flex-end" mb="xs">
              <Select
                size="xs"
                label="Tipo"
                data={SLOT_TYPE_OPTIONS}
                value={slot.slot_type}
                onChange={(v) => onChangeSlot({ slot_type: (v as WorkoutPlanTemplateSlotType) || 'normal' })}
                style={{ flex: '1 1 110px', minWidth: 0 }}
              />
              <Select
                size="xs"
                label="Dia da semana (informativo)"
                placeholder="Nenhum"
                clearable
                data={WEEKDAY_OPTIONS}
                value={slot.weekday != null ? String(slot.weekday) : null}
                onChange={(v) => onChangeSlot({ weekday: v ? Number(v) : null })}
                style={{ flex: '1 1 150px', minWidth: 0 }}
              />
            </Group>

            {safetyWarningCount > 0 && (
              <Tooltip label="Há itens deste slot com aviso de segurança — veja o aviso no card/linha do item.">
                <Badge color="red" variant="light" leftSection={<IconAlertTriangle size={12} />} mb="xs">
                  {safetyWarningCount} item(ns) a revisar
                </Badge>
              </Tooltip>
            )}

            {isNormalSlotWithoutItems(slot) && (
              // #318 — só avisa: o dashboard NÃO trata slot normal vazio como
              // descanso, então ele conta como treino esperado na aderência. A
              // ação troca o Tipo no formulário; nada é gravado até o "Salvar
              // modelo". Empilhado (texto em cima, botão embaixo) para caber
              // na célula estreita da grade.
              <Alert color="yellow" variant="light" icon={<IconInfoCircle size={16} />} mb="xs" p="xs">
                <Stack gap="xs" align="flex-start">
                  <Text size="sm">
                    Slot sem exercícios — é um dia de descanso? Como &quot;Normal&quot;, ele conta como treino
                    esperado na aderência.
                  </Text>
                  <Button
                    size="xs"
                    variant="light"
                    color="yellow"
                    onClick={() => onChangeSlot({ slot_type: 'descanso' })}
                  >
                    Marcar como descanso
                  </Button>
                </Stack>
              </Alert>
            )}

            {supportsBatchPct(volumeMode) && <SlotBatchPctShortcut onApply={onApplyBatchPct} />}

            {trainingGoals && slot.slot_type !== 'descanso' && slot.items.some(itemAcceptsGoal) && (
              <SlotGoalShortcut
                slot={slot}
                trainingGoals={trainingGoals}
                templateGoal={templateGoal ?? null}
                onChangeSlot={onChangeSlot}
              />
            )}

            <SortableContext items={itemIds} strategy={verticalListSortingStrategy}>
              <Stack gap="xs">
                {volumeMode === 'equipment_series' && slot.items.length > 0 && <SlotTotalTime items={slot.items} />}
                {slot.items.map((item, itemIndex) => (
                  <SortableItem key={item.localId} id={item.localId} slotLocalId={slot.localId}>
                    {(handle) =>
                      volumeMode === 'equipment_series' ? (
                        <EquipmentSeriesItemCard
                          item={item}
                          itemIndex={itemIndex}
                          dragHandle={handle}
                          safetyWarnings={safetyWarningsForItem(safetyWarnings, slotIndex, itemIndex, item)}
                          activation={itemActivationByKey.get(itemActivationKey(slotIndex + 1, itemIndex))}
                          priorFatigue={itemPriorFatigueByKey?.get(itemActivationKey(slotIndex + 1, itemIndex))}
                          directShare={itemDirectShareByKey?.get(itemActivationKey(slotIndex + 1, itemIndex))}
                          volumeMode={volumeMode}
                          trainingGoals={trainingGoals}
                          templateGoal={templateGoal ?? null}
                          slotSequence={slotIndex + 1}
                          methodologyEvaluation={methodologyEvaluation}
                          methodologyPending={methodologyPending}
                          muscleGroupOptions={muscleGroupOptions}
                          muscleGroupNameById={muscleGroupNameById}
                          expanded={expandedItemIds.has(item.localId)}
                          onToggleExpanded={() => onToggleItemExpanded(item.localId)}
                          collapsed={collapsedItemIds.has(item.localId)}
                          onToggleCollapsed={() => onToggleItemCollapsed(item.localId)}
                          onUpdate={(patch) => onChangeItem(item.localId, patch)}
                          onRemove={() => onRemoveItem(item.localId)}
                        />
                      ) : (
                        <PercentBudgetItemCard
                          item={item}
                          itemIndex={itemIndex}
                          slotType={slot.slot_type}
                          trainingTypeId={trainingTypeId}
                          trainingGoals={trainingGoals}
                          templateGoal={templateGoal ?? null}
                          muscleGroupOptions={muscleGroupOptions}
                          activation={itemActivationByKey.get(itemActivationKey(slotIndex + 1, itemIndex))}
                          priorFatigue={itemPriorFatigueByKey?.get(itemActivationKey(slotIndex + 1, itemIndex))}
                          safetyWarnings={safetyWarningsForItem(safetyWarnings, slotIndex, itemIndex, item)}
                          dragHandle={handle}
                          muscleGroupNameById={muscleGroupNameById}
                          collapsed={collapsedItemIds.has(item.localId)}
                          onToggleCollapsed={() => onToggleItemCollapsed(item.localId)}
                          onChangeItem={(patch) => onChangeItem(item.localId, patch)}
                          onRemoveItem={() => onRemoveItem(item.localId)}
                        />
                      )
                    }
                  </SortableItem>
                ))}
                {slot.items.length === 0 && (
                  <Text c={BRAND.textMuted} ta="center" py="sm" size="sm">
                    Nenhum item neste slot ainda.
                  </Text>
                )}
              </Stack>
            </SortableContext>

            <Group gap="xs" mt="xs">
              {volumeMode === 'equipment_series' ? (
                <Button
                  size="xs"
                  variant="light"
                  color="brand"
                  leftSection={<IconPlus size={12} />}
                  onClick={() => onAddItem('equipment')}
                  disabled={slot.slot_type === 'descanso'}
                >
                  Adicionar equipamento
                </Button>
              ) : (
                <Button
                  size="xs"
                  variant="light"
                  color="brand"
                  leftSection={<IconPlus size={12} />}
                  onClick={() => onAddItem('muscle_group')}
                  disabled={slot.slot_type === 'descanso'}
                >
                  Adicionar grupo muscular
                </Button>
              )}
              <Button
                size="xs"
                variant="light"
                color="brand"
                leftSection={<IconPlus size={12} />}
                onClick={() => onAddItem('cardio')}
              >
                Adicionar cardio
              </Button>
            </Group>
          </>
        )}
      </Paper>
    </div>
  );
}

// M97 F3: "Objetivo do dia" — aplica o MESMO override a todos os itens do slot
// (Modelo híbrido, ex.: 2 dias de força + 3 de hipertrofia). Sem nível novo de
// dado: grava item a item; depois cada item continua editável no card.
function SlotGoalShortcut({
  slot,
  trainingGoals,
  templateGoal,
  onChangeSlot,
}: {
  slot: SlotState;
  trainingGoals: TrainingGoalsResponse;
  templateGoal: string | null;
  onChangeSlot: (patch: Partial<SlotState>) => void;
}) {
  const common = slotCommonGoal(trainingGoals, slot.items);
  const effective = slotEffectiveGoals(trainingGoals, slot.items, templateGoal);
  return (
    <Group gap="xs" mb="xs" wrap="wrap" align="flex-end" data-testid="slot-goal-shortcut">
      <Select
        size="xs"
        label="Objetivo do dia"
        description="Aplica a todos os itens deste slot"
        data={itemGoalSelectData(trainingGoals, templateGoal)}
        value={common === undefined ? null : itemGoalSelectValue(trainingGoals, common)}
        placeholder="Misto (por item)"
        allowDeselect={false}
        onChange={(v) => {
          if (!v) return;
          onChangeSlot({ items: applySlotGoal(slot.items, itemGoalFromSelect(trainingGoals, v)) });
        }}
        style={{ flex: '1 1 160px' }}
      />
      <Group gap={4}>
        {effective.map((goal) => (
          <Badge key={goal.value} size="sm" variant="light" color={goalBadgeColor(goal)}>
            {goal.label}
          </Badge>
        ))}
      </Group>
    </Group>
  );
}
