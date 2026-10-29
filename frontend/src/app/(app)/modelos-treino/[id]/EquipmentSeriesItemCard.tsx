'use client';

import {
  ActionIcon,
  Alert,
  Badge,
  Box,
  Button,
  Checkbox,
  Collapse,
  Divider,
  Group,
  Paper,
  Select,
  Stack,
  Text,
  Tooltip,
  UnstyledButton,
} from '@mantine/core';
import { IconAlertTriangle, IconChevronDown, IconChevronUp, IconTrash } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { useMemo } from 'react';
import { PhotoLightbox, usePhotoLightbox } from '@/app/(fullscreen)/modo-treino/PhotoLightbox';
import { EquipmentPhoto } from '@/components/EquipmentPhoto';
import {
  getAdvancedTechniqueDefaultSetsCount,
  getAdvancedTechniqueLabel,
  getAdvancedTechniqueOptions,
  isSetsOnlyTechnique,
} from '@/lib/advanced-techniques';
import { apiFetch, ApiError } from '@/lib/api';
import type { ApiListResponse as EquipmentApiListResponse } from '@/lib/equipment-exercise-mappings-types';
import type { EquipmentExerciseLink } from '@/lib/equipment-exercises-types';
import type { ItemActivationSummary } from '@/lib/item-activation-summary';
import { buildItemDirectSetsShareLines, formatItemPlannedSetsNote } from '@/lib/methodology-card-share';
import type { MethodologyItemDirectShare, MethodologyVolumeEvaluation } from '@/lib/methodology-volume-types';
import { setsUnitOf } from '@/lib/volume-counting-mode';
import { techniqueCatalogFromItems } from '@/lib/technique-accounting';
import { useAdvancedTechniques } from '@/lib/use-advanced-techniques';
import {
  effectiveItemGoal,
  goalAllowsTechnique,
  itemFieldPlaceholders,
  type TrainingGoalsResponse,
} from '@/lib/training-goals';
import { isRepBased, MEASURE_LIMITS, MEASURE_VALUES, normalizeMeasure } from '@/lib/set-measures';
import {
  FREE_EXERCISE_PATCH,
  isFreeExerciseItem,
  itemMeasureSummary,
  MEASURE_SELECT_DATA,
  measureChangePatch,
  pairSelectionPatch,
  type ItemState,
} from '@/lib/workout-plan-template-form';
import type {
  AdvancedTechnique,
  PriorFatigue,
  TemplateSafetyWarning,
  VolumeMode,
} from '@/lib/workout-plan-templates-types';
import { BRAND } from '@/theme';
import { ItemActivationLine } from '../ItemActivationLine';
import { ItemDirectSetsLine } from '../ItemDirectSetsLine';
import { BlurCommitNumberInput, BlurCommitTextInput } from './BlurCommitInputs';
import { ItemCollapsedSummary, ItemCollapseToggle, itemCardStyle, PriorFatigueStripe } from './ItemCollapsedSummary';
import { ItemGoalSelect } from './ItemGoalSelect';
import { ItemSafetyWarnings } from './ItemSafetyWarnings';
import { DragHandle, type SortableHandle } from './sortable';
import { cardioShortDescribe } from '@/lib/cardio-prescription';
import { CardioPrescriptionEditor } from './CardioPrescriptionEditor';
import { FIELD_GRID_STYLE } from './fieldGridStyle';

// M101b: opção do Select de par para "O motor escolhe o exercício" (o item
// grava só o grupo, `mapping_id` e `equipment_id` NULL; a geração escolhe o
// exercício e o equipamento vem junto).
const FREE_EXERCISE_VALUE = 'free';
const FREE_EXERCISE_LABEL = 'O motor escolhe o exercício';
const FREE_EXERCISE_SUMMARY = 'Exercício livre — o motor escolhe';

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

// ---------------------------------------------------------------------------
// Card de item_kind='equipment' (modo volume_mode='equipment_series') —
// grupo muscular + equipamento + exercício + séries fixas, sem % de
// orçamento, sem IA escolhendo nada (usuário define tudo). O par
// equipamento+exercício é resolvido reaproveitando GET
// /equipment-exercises?primary_group=<nome do grupo> (endpoint canônico,
// mesma fonte da tela `/equipamentos-exercicios` desde o PR-05),
// filtrado pelo grupo muscular escolhido — evita inventar um endpoint novo
// e mantém equipment_id/mapping_id sempre consistentes com o catálogo
// canônico + associação global equipamento->exercício.
type EquipmentSeriesItemCardProps = {
  item: ItemState;
  // Posição 0-based deste item dentro do slot (mesmo índice usado para
  // reordenar — M9). Exibido como numeração sequencial 1-based no card,
  // puramente visual/derivado (não é enviado ao backend).
  itemIndex: number;
  // Avisos de segurança do backend para ESTE item (PR-12) — exibidos fora do
  // `Collapse`, para aparecerem também com o card colapsado.
  safetyWarnings: TemplateSafetyWarning[];
  // M11: ativação deste item por grupo vs. total do grupo (do servidor, via a
  // query compartilhada do preview). `undefined` = nada a mostrar.
  activation?: ItemActivationSummary;
  // Fadiga prévia do item no slot (preview, dono `domain/prior_fatigue.py`):
  // faixa de cor lateral. `undefined` = sem faixa.
  priorFatigue?: PriorFatigue | null;
  // M43: participação por séries (Modo B) — mesma query do painel de
  // metodologia; `slotSequence` casa `sessions[]` do bloco com este dia e
  // `directShare` (Lote 3) é a fatia deste item já no modo de contagem.
  directShare?: MethodologyItemDirectShare;
  volumeMode: VolumeMode;
  // M97 F3: objetivos de `GET /training-goals` e o do Modelo (override no item).
  trainingGoals?: TrainingGoalsResponse;
  templateGoal?: string | null;
  slotSequence: number;
  methodologyEvaluation?: MethodologyVolumeEvaluation;
  methodologyPending: boolean;
  muscleGroupOptions: { value: string; label: string }[];
  muscleGroupNameById: Map<number, string>;
  expanded: boolean;
  onToggleExpanded: () => void;
  // Card recolhido (resumo para organizar/arrastar). Independente de
  // `expanded`, que é só a seção "Configurações avançadas".
  collapsed: boolean;
  onToggleCollapsed: () => void;
  onUpdate: (patch: Partial<ItemState>) => void;
  onRemove: () => void;
  // Alça de arraste (M9), vinda do `SortableItem` do slot (dnd-kit).
  dragHandle?: SortableHandle;
};

export function EquipmentSeriesItemCard({
  item,
  itemIndex,
  dragHandle,
  safetyWarnings,
  activation,
  priorFatigue,
  directShare,
  volumeMode,
  trainingGoals,
  templateGoal = null,
  slotSequence,
  methodologyEvaluation,
  methodologyPending,
  muscleGroupOptions,
  muscleGroupNameById,
  expanded,
  onToggleExpanded,
  collapsed,
  onToggleCollapsed,
  onUpdate,
  onRemove,
}: EquipmentSeriesItemCardProps) {
  const isCardio = item.item_kind === 'cardio';
  // M97 F3: objetivo efetivo do item (espelho da precedência do dono) e os
  // defaults dele, prontos da API, nos placeholders.
  const goal = effectiveItemGoal(trainingGoals, item.training_goal, templateGoal);
  const placeholders = itemFieldPlaceholders(goal);
  const techniqueBlockedByGoal = !goalAllowsTechnique(goal, item.advanced_technique);
  const groupName = item.muscle_group_id != null ? muscleGroupNameById.get(item.muscle_group_id) || null : null;

  // Catálogo de técnicas (GET /advanced-techniques; fallback local se a
  // chamada falhar). Só as ativas + a já salva no item.
  const techniqueCatalog = useAdvancedTechniques();
  const techniqueOptions = getAdvancedTechniqueOptions(techniqueCatalog.items, item.advanced_technique);
  const techniqueCoefficients = useMemo(() => techniqueCatalogFromItems(techniqueCatalog.items), [techniqueCatalog.items]);
  // FST-7/GVT: só preenchem o nº de séries; aplicações e pausa ficam ocultas.
  const setsOnlyTechnique = isSetsOnlyTechnique(item.advanced_technique);
  // Fase 5 (PR 5): o card soma o equivalente PLANEJADO da técnica às séries.
  const itemTechnique = useMemo(
    () =>
      item.advanced_technique
        ? {
            code: item.advanced_technique,
            applications: item.advanced_technique_applications,
            catalog: techniqueCoefficients,
            label: getAdvancedTechniqueLabel(item.advanced_technique),
          }
        : null,
    [item.advanced_technique, item.advanced_technique_applications, techniqueCoefficients],
  );
  const techniqueNote = formatItemPlannedSetsNote(item.sets_count, itemTechnique);

  // Medida da série (Lote T4; espelho `lib/set-measures.ts`): fora de reps
  // não há alvo de reps nem bloco avançado (o servidor recusaria).
  const measure = normalizeMeasure(item.measure);
  const repBased = isRepBased(measure);

  // M43 + Lote 3: a fatia do item por bloco vem pronta do backend, no modo
  // de contagem do Modelo; o card só formata.
  const directSetsLines = useMemo(
    () =>
      buildItemDirectSetsShareLines({
        volumeMode,
        slotSequence,
        share: directShare,
        evaluation: methodologyEvaluation,
        techniqueNote,
      }),
    [volumeMode, slotSequence, directShare, methodologyEvaluation, techniqueNote],
  );

  const equipmentOptionsQuery = useQuery({
    queryKey: ['equipment-exercises-for-group', groupName],
    queryFn: () =>
      apiFetch<EquipmentApiListResponse<EquipmentExerciseLink>>(
        `/equipment-exercises?active_only=true&primary_group=${encodeURIComponent(groupName || '')}`,
      ),
    enabled: !isCardio && !!groupName,
  });

  // Contrato do item de template preservado: `item.mapping_id` guarda o
  // `exercise_id` do catálogo canônico (mesmo valor numérico — `exercise_catalog.id`
  // preserva o id legado), então só muda a origem do par, não o payload de save.
  // M101b: a 1ª opção é "O motor escolhe o exercício" (item só com o grupo).
  const pairs = equipmentOptionsQuery.data?.items || [];
  const equipmentSelectData = [
    { value: FREE_EXERCISE_VALUE, label: FREE_EXERCISE_LABEL },
    ...pairs.map((m) => ({
      value: `${m.equipment_id}:${m.exercise_id}`,
      label: `${m.equipment_name} — ${m.exercise_name || m.exercise_amazfit_name || `#${m.exercise_id}`}`,
    })),
  ];
  const freeExercise = !isCardio && isFreeExerciseItem(item);
  const equipmentSelectValue =
    item.equipment_id != null && item.mapping_id != null
      ? `${item.equipment_id}:${item.mapping_id}`
      : freeExercise
        ? FREE_EXERCISE_VALUE
        : null;

  // Equipamento+exercício selecionado nesta linha — usado para achar
  // `photo_url` (mesma fonte de GET /equipment-exercises já
  // carregada acima para o Select) e montar a miniatura clicável que abre o
  // PhotoLightbox, mesmo padrão já usado em SetLoggerCard (modo treino).
  const selectedMapping = pairs.find(
    (m) => m.equipment_id === item.equipment_id && m.exercise_id === item.mapping_id,
  );
  const equipmentPhotoUrl = selectedMapping?.photo_url ?? null;
  const lightbox = usePhotoLightbox();
  const equipmentPhotoAlt = selectedMapping?.equipment_name || 'Equipamento';

  if (collapsed) {
    const equipmentChosen = !isCardio && item.equipment_id != null;
    let detail: string;
    if (isCardio) {
      // M98 C1: resumo do dono (`describe_short`, espelho com paridade).
      detail = cardioShortDescribe(item);
    } else if (freeExercise) {
      detail = FREE_EXERCISE_SUMMARY;
    } else if (!equipmentChosen) {
      detail = 'Equipamento não escolhido';
    } else {
      detail = selectedMapping?.equipment_name ?? (equipmentOptionsQuery.isPending ? 'Carregando equipamento…' : 'Equipamento');
    }
    return (
      <Paper withBorder radius="md" p="sm" style={itemCardStyle(itemIndex)}>
        <PriorFatigueStripe priorFatigue={priorFatigue} />
        <ItemCollapsedSummary
          itemIndex={itemIndex}
          dragHandle={dragHandle}
          title={isCardio ? 'Cardio' : groupName || 'Grupo muscular não escolhido'}
          detail={detail}
          exercise={
            selectedMapping ? selectedMapping.exercise_name || selectedMapping.exercise_amazfit_name || null : null
          }
          detailMuted={!isCardio && !selectedMapping && !freeExercise}
          photo={
            equipmentChosen
              ? {
                  url: equipmentPhotoUrl,
                  alt: equipmentPhotoAlt,
                  onOpen: () => equipmentPhotoUrl && lightbox.open({ src: equipmentPhotoUrl, alt: equipmentPhotoAlt }),
                }
              : null
          }
          activation={activation}
          techniqueLabel={isCardio ? null : itemTechnique?.label}
          measureText={isCardio ? null : itemMeasureSummary(item)}
          warmupSets={isCardio ? null : item.warmup_sets_count}
          feelerSets={isCardio ? null : item.feeler_set_sets_count}
          onExpand={onToggleCollapsed}
        />
        <PhotoLightbox photo={lightbox.photo} onClose={lightbox.close} />
      </Paper>
    );
  }

  if (isCardio) {
    return (
      <Paper withBorder radius="md" p="sm" style={itemCardStyle(itemIndex)}>
        <Stack gap="xs">
          {/* Linha 1: alça, número, tipo, recolher e lixeira. */}
          <Group justify="space-between" wrap="nowrap" gap="xs">
            <Group gap="xs" wrap="nowrap">
              <DragHandle handle={dragHandle} label="Arrastar para reordenar" />
              <Badge size="sm" circle variant="light" color="gray" aria-label={`Exercício número ${itemIndex + 1} do slot`}>
                {itemIndex + 1}
              </Badge>
              <Badge color="gray" variant="light">
                Cardio
              </Badge>
            </Group>
            <Group gap={4} wrap="nowrap">
              <ItemCollapseToggle collapsed={false} onToggle={onToggleCollapsed} />
              <ActionIcon size="sm" color="red" variant="subtle" onClick={onRemove} aria-label="Remover item">
                <IconTrash size={14} />
              </ActionIcon>
            </Group>
          </Group>
          {/* Linha 2: prescrição Normal / HIIT (M98 C1). */}
          <CardioPrescriptionEditor item={item} onUpdate={onUpdate} />
          {/* Linha 3: priorizado. */}
          <Group gap="xs" wrap="wrap" align="center">
            <Checkbox
              size="xs"
              label="Priorizado"
              checked={item.prioritize}
              onChange={(e) => onUpdate({ prioritize: e.currentTarget.checked })}
            />
          </Group>
        </Stack>
        <ItemSafetyWarnings warnings={safetyWarnings} />
      </Paper>
    );
  }

  return (
    <Paper withBorder radius="md" p="sm" style={itemCardStyle(itemIndex)}>
      <PriorFatigueStripe priorFatigue={priorFatigue} />
      <Stack gap="xs">
        {/* Linha 1: alça, número, foto, grupo muscular e recolher o card. */}
        <Group align="flex-end" wrap="nowrap" gap="xs">
          <DragHandle handle={dragHandle} label="Arrastar para reordenar" />
          <Badge size="sm" circle variant="light" color="gray" aria-label={`Exercício número ${itemIndex + 1} do slot`}>
            {itemIndex + 1}
          </Badge>
          {item.equipment_id != null && (
            <UnstyledButton
              onClick={() => equipmentPhotoUrl && lightbox.open({ src: equipmentPhotoUrl, alt: equipmentPhotoAlt })}
              disabled={!equipmentPhotoUrl}
              aria-label="Ampliar foto do equipamento"
              style={{ cursor: equipmentPhotoUrl ? 'pointer' : 'default', flexShrink: 0 }}
            >
              <EquipmentPhoto photoUrl={equipmentPhotoUrl} alt={equipmentPhotoAlt} size={36} />
            </UnstyledButton>
          )}
          <Select
            size="xs"
            label="Grupo muscular"
            placeholder="Selecione..."
            searchable
            data={muscleGroupOptions}
            value={item.muscle_group_id != null ? String(item.muscle_group_id) : null}
            onChange={(v) =>
              onUpdate({
                muscle_group_id: v ? Number(v) : null,
                // Grupo mudou: equipamento/exercício antigo pode não valer
                // mais para o novo grupo — força nova escolha.
                equipment_id: null,
                mapping_id: null,
              })
            }
            style={{ flex: 1, minWidth: 0 }}
          />
          <ItemCollapseToggle collapsed={false} onToggle={onToggleCollapsed} />
        </Group>

        {/* Linha 2: equipamento — exercício, ou "O motor escolhe o exercício" (M101b). */}
        <Select
          size="xs"
          label="Equipamento — Exercício"
          placeholder={groupName ? 'Selecione...' : 'Selecione o grupo primeiro'}
          searchable
          allowDeselect={false}
          disabled={!groupName}
          data={equipmentSelectData}
          value={equipmentSelectValue}
          onChange={(v) => {
            if (!v || v === FREE_EXERCISE_VALUE) {
              onUpdate(FREE_EXERCISE_PATCH);
              return;
            }
            const [equipmentIdRaw, mappingIdRaw] = v.split(':');
            const equipmentId = Number(equipmentIdRaw);
            const mappingId = Number(mappingIdRaw);
            const pair = pairs.find((m) => m.equipment_id === equipmentId && m.exercise_id === mappingId);
            onUpdate(pairSelectionPatch(item, equipmentId, mappingId, pair?.default_measure));
          }}
          w="100%"
          nothingFoundMessage={
            groupName && equipmentOptionsQuery.isSuccess
              ? 'Nenhum equipamento associado a este grupo (ver /equipamentos-exercicios).'
              : undefined
          }
        />

        {/* Linha 3: medida, séries, reps (ou tempo/janela), RPE e descanso. */}
        <Box style={FIELD_GRID_STYLE}>
          <ItemGoalSelect
            trainingGoals={trainingGoals}
            templateGoal={templateGoal}
            value={item.training_goal}
            onChange={(training_goal) => onUpdate({ training_goal })}
          />
          <Select
            size="xs"
            label="Medida"
            data={MEASURE_SELECT_DATA}
            value={measure}
            allowDeselect={false}
            onChange={(v) => {
              if (v && v !== measure) onUpdate(measureChangePatch(v));
            }}
          />
          <BlurCommitNumberInput
            size="xs"
            label="Séries de trabalho"
            placeholder={placeholders?.sets}
            value={item.sets_count ?? undefined}
            onCommit={(v) => onUpdate({ sets_count: typeof v === 'number' ? v : null })}
            min={1}
          />
          {repBased && (
            <BlurCommitTextInput
              size="xs"
              label="Reps"
              placeholder={placeholders?.reps ?? 'ex.: 8-12'}
              value={item.reps_target ?? ''}
              onCommit={(v) => onUpdate({ reps_target: v || null })}
            />
          )}
          {measure === MEASURE_VALUES.duration && (
            <BlurCommitNumberInput
              size="xs"
              label="Tempo por série (s)"
              required
              value={item.duration_seconds_target ?? undefined}
              onCommit={(v) => onUpdate({ duration_seconds_target: typeof v === 'number' ? v : null })}
              min={MEASURE_LIMITS.durationSecondsMin}
              max={MEASURE_LIMITS.durationSecondsMax}
              allowDecimal={false}
            />
          )}
          {measure === MEASURE_VALUES.max_reps && (
            <BlurCommitNumberInput
              size="xs"
              label="Janela (s, opcional)"
              placeholder="livre"
              value={item.time_window_seconds ?? undefined}
              onCommit={(v) => onUpdate({ time_window_seconds: typeof v === 'number' ? v : null })}
              min={MEASURE_LIMITS.timeWindowSecondsMin}
              max={MEASURE_LIMITS.timeWindowSecondsMax}
              allowDecimal={false}
            />
          )}
          <BlurCommitTextInput
            size="xs"
            label="RPE"
            placeholder={placeholders?.rpe ?? 'ex.: 7-8'}
            value={item.rpe_target ?? ''}
            onCommit={(v) => onUpdate({ rpe_target: v || null })}
          />
          {/* Fase 7: descanso entre as séries de trabalho deste exercício.
              Vazio = "da geração" (ponto médio do intervalo escolhido ao
              gerar a ficha); preenchido, sobrepõe o intervalo. */}
          <BlurCommitNumberInput
            size="xs"
            label="Descanso (s)"
            placeholder={placeholders?.rest ?? 'da geração'}
            value={item.rest_seconds ?? undefined}
            onCommit={(v) => onUpdate({ rest_seconds: typeof v === 'number' ? v : null })}
            min={1}
            max={600}
          />
        </Box>

        {/* Linha 4: priorizado, configurações avançadas, "Revisar" e lixeira. */}
        <Group justify="space-between" wrap="wrap" gap={4} align="center">
          <Checkbox
            size="xs"
            label="Priorizado"
            checked={item.prioritize}
            onChange={(e) => onUpdate({ prioritize: e.currentTarget.checked })}
          />
          <Group gap={4} wrap="wrap" justify="flex-end">
            {!expanded && safetyWarnings.length > 0 && (
              <Tooltip label="Este item tem aviso de segurança — veja o detalhe logo abaixo.">
                <Badge color="red" variant="light" leftSection={<IconAlertTriangle size={12} />}>
                  Revisar
                </Badge>
              </Tooltip>
            )}
            {repBased && (
              <Button
                size="xs"
                variant="subtle"
                color="brand"
                rightSection={expanded ? <IconChevronUp size={14} /> : <IconChevronDown size={14} />}
                onClick={onToggleExpanded}
                styles={{ label: { whiteSpace: 'normal', textAlign: 'left' } }}
                h="auto"
                py={4}
              >
                Técnica avançada / aquecimento / ajuste
              </Button>
            )}
            <ActionIcon size="sm" color="red" variant="subtle" onClick={onRemove} aria-label="Remover item">
              <IconTrash size={14} />
            </ActionIcon>
          </Group>
        </Group>
      </Stack>

      <ItemActivationLine summary={activation} />
      <ItemDirectSetsLine
        lines={directSetsLines}
        techniqueNote={techniqueNote}
        unitLabel={setsUnitOf(methodologyEvaluation?.counting_mode).plural}
        pending={methodologyPending && volumeMode === 'equipment_series' && (activation?.primary.length ?? 0) > 0}
      />

      {equipmentOptionsQuery.isError && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />} mt="xs">
          {errorMessage(equipmentOptionsQuery.error, 'Não foi possível carregar equipamentos para este grupo.')}
        </Alert>
      )}

      <ItemSafetyWarnings warnings={safetyWarnings} />

      <Collapse in={expanded && repBased}>
        <Divider my="sm" label="Configurações avançadas (opcionais)" labelPosition="left" />
        <Stack gap="sm">
          <Stack gap="xs">
            <Select
              size="xs"
              label="Técnica avançada"
              placeholder="Nenhuma"
              clearable
              data={techniqueOptions}
              value={item.advanced_technique}
              onChange={(v) => {
                const code = (v as AdvancedTechnique) || null;
                const defaultSets = getAdvancedTechniqueDefaultSetsCount(code);
                // FST-7/GVT: preenche as séries com o padrão do catálogo (7/10,
                // continua editável) e zera aplicações/pausa, que não existem nelas.
                onUpdate(
                  defaultSets != null
                    ? {
                        advanced_technique: code,
                        sets_count: defaultSets,
                        advanced_technique_applications: null,
                        advanced_technique_duration_seconds: null,
                      }
                    : { advanced_technique: code },
                );
              }}
              w="100%"
            />
            {techniqueBlockedByGoal && goal && (
              <Alert color="yellow" variant="light" p="xs" data-testid="technique-blocked-by-goal">
                <Text size="xs">
                  Objetivo {goal.label}: o motor não aplica {getAdvancedTechniqueLabel(item.advanced_technique)} neste
                  item (a técnica sai da prescrição da ficha).
                </Text>
              </Alert>
            )}
            {setsOnlyTechnique ? (
              <Text size="xs" c={BRAND.textMuted} data-testid="sets-only-technique-hint">
                {getAdvancedTechniqueLabel(item.advanced_technique)} define só o número de séries (padrão{' '}
                {getAdvancedTechniqueDefaultSetsCount(item.advanced_technique)}); não há aplicações nem pausa.
              </Text>
            ) : (
              <Box style={FIELD_GRID_STYLE}>
                <BlurCommitNumberInput
                  size="xs"
                  label="Nº de aplicações"
                  description="drops/mini-blocos/reps extras"
                  value={item.advanced_technique_applications ?? undefined}
                  onCommit={(v) => onUpdate({ advanced_technique_applications: typeof v === 'number' ? v : null })}
                  min={1}
                  max={20}
                  disabled={!item.advanced_technique}
                />
                <BlurCommitNumberInput
                  size="xs"
                  label="Pausa (segundos)"
                  value={item.advanced_technique_duration_seconds ?? undefined}
                  onCommit={(v) => onUpdate({ advanced_technique_duration_seconds: typeof v === 'number' ? v : null })}
                  min={1}
                  max={600}
                  disabled={!item.advanced_technique}
                />
              </Box>
            )}
          </Stack>

          <Stack gap={4}>
            <Text size="xs" c={BRAND.textMuted}>
              Aquecimento
            </Text>
            <Box style={FIELD_GRID_STYLE}>
            <BlurCommitNumberInput
              size="xs"
              label="Séries"
              value={item.warmup_sets_count ?? undefined}
              onCommit={(v) => onUpdate({ warmup_sets_count: typeof v === 'number' ? v : null })}
              min={1}
              max={10}
            />
            <BlurCommitNumberInput
              size="xs"
              label="Reps"
              value={item.warmup_reps ?? undefined}
              onCommit={(v) => onUpdate({ warmup_reps: typeof v === 'number' ? v : null })}
              min={1}
              max={100}
            />
            <BlurCommitNumberInput
              size="xs"
              label="Descanso (s)"
              value={item.warmup_rest_seconds ?? undefined}
              onCommit={(v) => onUpdate({ warmup_rest_seconds: typeof v === 'number' ? v : null })}
              min={1}
              max={600}
            />
            </Box>
          </Stack>

          <Stack gap={4}>
            <Text size="xs" c={BRAND.textMuted}>
              Série de ajuste
            </Text>
            <Box style={FIELD_GRID_STYLE}>
            <BlurCommitNumberInput
              size="xs"
              label="Séries"
              value={item.feeler_set_sets_count ?? undefined}
              onCommit={(v) => onUpdate({ feeler_set_sets_count: typeof v === 'number' ? v : null })}
              min={1}
              max={10}
            />
            <BlurCommitNumberInput
              size="xs"
              label="Reps"
              value={item.feeler_set_reps ?? undefined}
              onCommit={(v) => onUpdate({ feeler_set_reps: typeof v === 'number' ? v : null })}
              min={1}
              max={100}
            />
            <BlurCommitNumberInput
              size="xs"
              label="Descanso (s)"
              value={item.feeler_set_rest_seconds ?? undefined}
              onCommit={(v) => onUpdate({ feeler_set_rest_seconds: typeof v === 'number' ? v : null })}
              min={1}
              max={600}
            />
            </Box>
          </Stack>

          <Group align="flex-end" wrap="wrap" gap="xs">
            <Checkbox
              size="xs"
              label="Pico de contração"
              checked={item.peak_contraction_seconds != null}
              onChange={(e) => onUpdate({ peak_contraction_seconds: e.currentTarget.checked ? 2 : null })}
            />
            {item.peak_contraction_seconds != null && (
              <BlurCommitNumberInput
                size="xs"
                label="Segundos"
                value={item.peak_contraction_seconds ?? undefined}
                onCommit={(v) => onUpdate({ peak_contraction_seconds: typeof v === 'number' ? v : 2 })}
                min={1}
                max={60}
                w={100}
              />
            )}
          </Group>
        </Stack>
      </Collapse>

      <PhotoLightbox photo={lightbox.photo} onClose={lightbox.close} />
    </Paper>
  );
}
