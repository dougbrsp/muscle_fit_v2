'use client';

import {
  Autocomplete,
  Badge,
  Button,
  Group,
  Loader,
  NumberInput,
  Stack,
  Switch,
  Text,
  Textarea,
  TextInput,
} from '@mantine/core';
import type { ComboboxItem, OptionsFilter } from '@mantine/core';
import { useDebouncedValue } from '@mantine/hooks';
import { IconArrowDown, IconArrowUp, IconPlus, IconReplace, IconSettings, IconTrash } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';
import {
  advancedConfigBadges,
  isSavedRow,
  prescriptionBadges,
  progressionBadge,
  formatActivationList,
} from '@/app/(app)/planos-treino/ExercisesTable';
import { AdvancedConfigModal, type AdvancedConfigPatch } from '@/app/(app)/planos-treino/AdvancedConfigModal';
import { toActivationPayloadItem } from '@/lib/activation-payload';
import { apiFetch } from '@/lib/api';
import type { MuscleActivationBreakdownItem } from '@/lib/analytics-types';
import { filterBySwapEligibility, swapEligibilityFor, type SwapEligibilityIndex } from '@/lib/exercise-swap-options';
import type { MappingCanonicalOptionsResponse } from '@/lib/mappings-types';
import { DEFAULT_REPS_TARGET, DEFAULT_RPE_TARGET } from '@/lib/prescription-defaults';
import { suggestedProgressionTargets } from '@/lib/suggested-progression';
import { useAdvancedTechniques } from '@/lib/use-advanced-techniques';
import type { SuggestedProgressionResponse, WorkoutPlanExercise } from '@/lib/workout-plans-types';
import { PortalFieldsStyle } from './PortalFieldsStyle';
import { EmptyState, ListBlock, ListGroup, ListRow, ScreenHeader, StickyAction } from '../../ui';

const showAllOptions: OptionsFilter = ({ options }) => options;

type Props = {
  exercises: WorkoutPlanExercise[];
  onChange: (exercises: WorkoutPlanExercise[]) => void;
  readOnly?: boolean;
  swapOptions?: SwapEligibilityIndex | 'loading';
  onSwapEquipment?: (exercise: WorkoutPlanExercise) => void;
  onRefreshPrescription?: () => void;
  refreshingPrescription?: boolean;
  refreshPrescriptionError?: string | null;
  /** Avisa o pai quando a edição de uma linha ocupa a tela inteira. */
  onEditingChange?: (editing: boolean) => void;
};

/**
 * Exercícios de uma ficha no celular: lista (1 dado principal por linha) e,
 * ao tocar, edição de página inteira com os MESMOS campos, endpoints e
 * efeitos da `ExercisesTable` do desktop (mesmas chaves de query, sugestão de
 * progressão do servidor, grupo travado em linha salva, config avançada).
 * Nenhum cálculo: reps/carga/RPE sugeridos e avisos vêm prontos da API.
 */
export function PlanExercisesEditor({
  exercises,
  onChange,
  readOnly = false,
  swapOptions,
  onSwapEquipment,
  onRefreshPrescription,
  refreshingPrescription = false,
  refreshPrescriptionError = null,
  onEditingChange,
}: Props) {
  useAdvancedTechniques();
  const [editIndex, setEditIndex] = useState<number | null>(null);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [suggestionLoading, setSuggestionLoading] = useState(false);
  const [typing, setTyping] = useState(false);

  const exercisesRef = useRef(exercises);
  useEffect(() => {
    exercisesRef.current = exercises;
  }, [exercises]);

  function setEditing(index: number | null) {
    setEditIndex(index);
    onEditingChange?.(index !== null);
  }

  const catalogQuery = useQuery({
    queryKey: ['mapping-canonical-options', 'all'],
    queryFn: () => apiFetch<MappingCanonicalOptionsResponse>('/mapping-canonical/options'),
    staleTime: 5 * 60_000,
  });
  const catalogItems = useMemo(() => catalogQuery.data?.items || [], [catalogQuery.data]);
  const autocompleteData = useMemo(
    () =>
      catalogItems
        .filter((item) => item.exercise_name)
        .map((item) => ({
          value: item.exercise_name as string,
          label: item.exercise_name as string,
          mappingId: item.id,
          amazfitName: item.amazfit_name,
          primaryGroup: item.primary_group,
        })),
    [catalogItems],
  );
  const primaryGroupByMappingId = useMemo(() => {
    const map = new Map<number, string>();
    for (const item of catalogItems) if (item.primary_group) map.set(item.id, item.primary_group);
    return map;
  }, [catalogItems]);
  const mappingIdByName = useMemo(() => {
    const map = new Map<string, number>();
    for (const item of catalogItems) if (item.exercise_name) map.set(item.exercise_name, item.id);
    return map;
  }, [catalogItems]);

  const optionsFilter: OptionsFilter = ({ options, search }) => {
    const needle = search.trim().toLowerCase();
    if (!needle) return options;
    return options.filter((option) => {
      if ('group' in option) return true;
      const item = option as ComboboxItem & { amazfitName?: string | null; primaryGroup?: string | null };
      return (
        item.label.toLowerCase().includes(needle) ||
        (item.amazfitName || '').toLowerCase().includes(needle) ||
        (item.primaryGroup || '').toLowerCase().includes(needle)
      );
    });
  };

  const breakdownSerialized = JSON.stringify({ exercises: exercises.map(toActivationPayloadItem) });
  const [debouncedBreakdown] = useDebouncedValue(breakdownSerialized, 500);
  const hasMapped = exercises.some((ex) => ex.mapping_id != null && (ex.sets_count ?? 0) > 0);
  const breakdownQuery = useQuery({
    queryKey: ['muscle-activation-breakdown', debouncedBreakdown],
    queryFn: () =>
      apiFetch<{ items: MuscleActivationBreakdownItem[] }>('/analytics/muscle-activation-breakdown', {
        method: 'POST',
        body: debouncedBreakdown,
      }),
    enabled: hasMapped,
  });
  const breakdowns = breakdownQuery.data?.items || [];

  function updateExercise(index: number, patch: Partial<WorkoutPlanExercise>) {
    const next = exercises.slice();
    next[index] = { ...next[index], ...patch };
    onChange(next);
  }

  async function applySuggestedProgression(index: number, mappingId: number) {
    setSuggestionLoading(true);
    try {
      const suggestion = await apiFetch<SuggestedProgressionResponse>(
        `/exercises/suggested-progression?mapping_id=${mappingId}`,
      );
      const current = exercisesRef.current;
      if (index >= current.length) return;
      const next = current.slice();
      const targets = suggestedProgressionTargets(suggestion);
      next[index] = {
        ...next[index],
        reps_target: targets.reps_target,
        load_target: targets.load_target,
        rpe_target: targets.rpe_target,
        progression_note: targets.progression_note,
        progression: suggestion.progression ?? null,
      };
      onChange(next);
    } catch {
      // silencioso: os campos continuam como estavam (igual ao desktop).
    } finally {
      setSuggestionLoading(false);
    }
  }

  function removeExercise(index: number) {
    const next = exercises.filter((_, i) => i !== index);
    next.forEach((ex, i) => {
      ex.sequence = i + 1;
    });
    onChange(next);
  }

  function moveExercise(from: number, to: number) {
    if (to < 0 || to >= exercises.length || to === from) return;
    const next = exercises.slice();
    const [moved] = next.splice(from, 1);
    next.splice(to, 0, moved);
    next.forEach((ex, i) => {
      ex.sequence = i + 1;
    });
    onChange(next);
    setEditIndex(to);
  }

  function addExercise() {
    onChange(
      exercises.concat({
        sequence: exercises.length + 1,
        exercise_name: '',
        muscle_primary: '',
        sets_count: 3,
        reps_target: '',
        warmup: false,
      }),
    );
    setEditing(exercises.length);
  }

  const hasLegacy = exercises.some((ex) => ex.prescription_status === 'LEGACY');

  // ---------------- edição de uma linha ----------------
  const exercise = editIndex !== null ? exercises[editIndex] : null;
  if (editIndex !== null && exercise) {
    const index = editIndex;
    const swapEligibility = swapEligibilityFor(exercise.workout_plan_exercise_id, swapOptions);
    const rowOptions = filterBySwapEligibility(autocompleteData, swapEligibility);
    const prescBadges = prescriptionBadges(exercise);
    const configBadges = advancedConfigBadges(exercise);
    const breakdown = breakdowns[index];
    const hasBreakdown =
      !!breakdown &&
      (breakdown.primary_activation > 0 || breakdown.secondary.length > 0 || breakdown.stabilizer.length > 0);
    const fmt = (v: number) => v.toLocaleString('pt-BR', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
    return (
      <>
        <ScreenHeader title={exercise.exercise_name || 'Novo exercício'} subtitle={`Exercício ${exercise.sequence} de ${exercises.length}`} />
        <Stack gap="md" pb="md">
          <Autocomplete
            size="md"
            label="Exercício"
            disabled={readOnly}
            data={rowOptions}
            filter={typing ? optionsFilter : showAllOptions}
            onFocus={() => setTyping(false)}
            onClick={() => setTyping(false)}
            rightSection={swapEligibility.kind === 'loading' || suggestionLoading ? <Loader size="xs" /> : undefined}
            value={exercise.exercise_name}
            onChange={(value) => {
              setTyping(true);
              const matches = exercise.mapping_id != null && mappingIdByName.get(value) === exercise.mapping_id;
              updateExercise(index, { exercise_name: value, mapping_id: matches ? exercise.mapping_id : null });
            }}
            onOptionSubmit={(value) => {
              const mappingId = mappingIdByName.get(value) ?? null;
              const catalogGroup = mappingId != null ? primaryGroupByMappingId.get(mappingId) : undefined;
              const groupPatch =
                !isSavedRow(exercise) && !exercise.muscle_primary && catalogGroup ? { muscle_primary: catalogGroup } : {};
              updateExercise(index, { exercise_name: value, mapping_id: mappingId, ...groupPatch });
              if (mappingId != null) void applySuggestedProgression(index, mappingId);
            }}
            renderOption={({ option }) => {
              const item = option as ComboboxItem & { amazfitName?: string | null; primaryGroup?: string | null };
              return (
                <div>
                  <Text size="sm">{item.label}</Text>
                  {item.amazfitName || item.primaryGroup ? (
                    <Text size="xs" c="dimmed">
                      {[item.amazfitName, item.primaryGroup].filter(Boolean).join(' — ')}
                    </Text>
                  ) : null}
                </div>
              );
            }}
            placeholder="Nome do exercício"
            limit={swapEligibility.kind === 'restricted' ? undefined : 20}
          />
          {exercise.equipment_name ? (
            <Text size="sm" c="dimmed">
              Equipamento: {exercise.equipment_name}
            </Text>
          ) : null}
          {isSavedRow(exercise) ? (
            <Text size="sm" c="dimmed">
              Grupo primário: {exercise.muscle_primary || '—'} (o grupamento muscular não pode ser alterado na edição da ficha)
            </Text>
          ) : (
            <TextInput
              size="md"
              label="Grupo primário"
              disabled={readOnly}
              value={exercise.muscle_primary || ''}
              onChange={(e) => updateExercise(index, { muscle_primary: e.currentTarget.value })}
              placeholder="Grupo muscular"
            />
          )}
          <Group grow align="flex-start">
            <NumberInput
              size="md"
              label="Séries"
              inputMode="numeric"
              disabled={readOnly}
              value={exercise.sets_count ?? undefined}
              onChange={(v) => updateExercise(index, { sets_count: typeof v === 'number' ? v : null })}
              min={1}
              max={20}
            />
            <NumberInput
              size="md"
              label="Descanso (s)"
              inputMode="numeric"
              disabled={readOnly}
              value={exercise.rest_seconds ?? undefined}
              onChange={(v) => updateExercise(index, { rest_seconds: typeof v === 'number' ? v : null })}
              min={15}
              max={240}
            />
          </Group>
          <TextInput
            size="md"
            label="Reps"
            disabled={readOnly}
            value={exercise.reps_target || ''}
            onChange={(e) => updateExercise(index, { reps_target: e.currentTarget.value })}
            placeholder={suggestionLoading ? 'Calculando...' : DEFAULT_REPS_TARGET}
          />
          <Group grow align="flex-start">
            <TextInput
              size="md"
              label="Carga"
              disabled={readOnly}
              value={exercise.load_target || ''}
              onChange={(e) => updateExercise(index, { load_target: e.currentTarget.value })}
              placeholder={
                suggestionLoading ? 'Calculando...' : exercise.suggested_load_kg ? `${exercise.suggested_load_kg} kg (sugerido)` : ''
              }
            />
            <TextInput
              size="md"
              label="RPE"
              disabled={readOnly}
              value={exercise.rpe_target || ''}
              onChange={(e) => updateExercise(index, { rpe_target: e.currentTarget.value })}
              placeholder={suggestionLoading ? 'Calculando...' : DEFAULT_RPE_TARGET}
            />
          </Group>
          <Switch
            size="md"
            label="Aquecimento"
            disabled={readOnly}
            checked={!!exercise.warmup}
            onChange={(e) => updateExercise(index, { warmup: e.currentTarget.checked })}
          />
          <Textarea
            size="md"
            label="Notas"
            autosize
            minRows={2}
            disabled={readOnly}
            value={exercise.notes || ''}
            onChange={(e) => updateExercise(index, { notes: e.currentTarget.value })}
          />

          <ListGroup title="Progressão e prescrição">
            <ListBlock>
              <Group gap="xs">{progressionBadge(exercise.progression)}</Group>
              {exercise.progression_note ? (
                <Text size="sm" c="dimmed">
                  {exercise.progression_note}
                </Text>
              ) : null}
            </ListBlock>
            {prescBadges.map((b) => (
              <ListRow key={b.key} title={<Badge color={b.color} variant="light">{b.label}</Badge>} subtitle={b.tooltip} />
            ))}
            {prescBadges.length === 0 ? <ListRow title="Sem avisos de prescrição" /> : null}
          </ListGroup>

          <ListGroup title="Configuração avançada">
            {configBadges.map((b) => (
              <ListRow key={b.key} title={<Badge color={b.color} variant="light">{b.label}</Badge>} subtitle={b.tooltip} />
            ))}
            {configBadges.length === 0 ? <ListRow title="Nenhuma configuração avançada" /> : null}
            {!readOnly ? (
              <ListRow icon={IconSettings} title="Editar técnica, aquecimento, ajuste e pico" onClick={() => setAdvancedOpen(true)} />
            ) : null}
          </ListGroup>

          {hasBreakdown ? (
            <ListGroup title="Ativação deste exercício">
              <ListBlock>
                <Text size="sm">Primária: {fmt(breakdown.primary_activation)}</Text>
                {breakdown.secondary.length > 0 ? (
                  <Text size="sm">Secundários: {formatActivationList(breakdown.secondary)}</Text>
                ) : null}
                {breakdown.stabilizer.length > 0 ? (
                  <Text size="sm">Estabilizadores: {formatActivationList(breakdown.stabilizer)}</Text>
                ) : null}
              </ListBlock>
            </ListGroup>
          ) : null}

          {!readOnly ? (
            <ListGroup title="Ações">
              <ListRow icon={IconArrowUp} title="Mover para cima" onClick={index === 0 ? undefined : () => moveExercise(index, index - 1)} />
              <ListRow
                icon={IconArrowDown}
                title="Mover para baixo"
                onClick={index === exercises.length - 1 ? undefined : () => moveExercise(index, index + 1)}
              />
              {onSwapEquipment && exercise.workout_plan_exercise_id != null && exercise.mapping_id != null ? (
                <ListRow
                  icon={IconReplace}
                  title="Trocar equipamento"
                  subtitle="Só o equipamento; o exercício não muda"
                  onClick={() => onSwapEquipment(exercise)}
                />
              ) : null}
              <ListRow
                icon={IconTrash}
                title="Remover exercício"
                destructive
                onClick={() => {
                  removeExercise(index);
                  setEditing(null);
                }}
              />
            </ListGroup>
          ) : null}
        </Stack>
        <StickyAction>
          <Button fullWidth size="lg" color="brand" mih={48} onClick={() => setEditing(null)}>
            Concluir
          </Button>
        </StickyAction>
        <PortalFieldsStyle />
        <AdvancedConfigModal
          exercise={advancedOpen ? exercise : null}
          onClose={() => setAdvancedOpen(false)}
          onApply={(patch: AdvancedConfigPatch) => updateExercise(index, patch)}
        />
      </>
    );
  }

  // ---------------- lista ----------------
  return (
    <>
      {onRefreshPrescription && !readOnly ? (
        <ListGroup
          title="Prescrição"
          footer={
            hasLegacy
              ? 'Esta ficha tem exercícios com prescrição legada, de antes do congelamento. Atualizar recalcula a prescrição de todos eles.'
              : 'A prescrição desta ficha está congelada. Atualizar recalcula tudo com o histórico e a segurança de hoje.'
          }
        >
          <ListRow
            title={refreshingPrescription ? 'Atualizando...' : 'Atualizar prescrição'}
            onClick={refreshingPrescription ? undefined : onRefreshPrescription}
          />
        </ListGroup>
      ) : null}
      {refreshPrescriptionError ? (
        <Text size="sm" c="red" mb="sm" role="alert">
          {refreshPrescriptionError}
        </Text>
      ) : null}
      <ListGroup title="Exercícios" footer={`${exercises.length} itens`}>
        {exercises.length === 0 ? (
          <ListBlock>
            <EmptyState title="Nenhum exercício nesta ficha ainda." />
          </ListBlock>
        ) : (
          exercises.map((ex, index) => (
            <ListRow
              key={index}
              title={`${ex.sequence}. ${ex.exercise_name || 'Novo exercício'}`}
              subtitle={[ex.muscle_primary, ex.equipment_name].filter(Boolean).join(' · ')}
              value={[ex.sets_count ? `${ex.sets_count}×${ex.reps_target || ''}` : null, ex.load_target].filter(Boolean).join(' ')}
              onClick={() => setEditing(index)}
            />
          ))
        )}
      </ListGroup>
      {!readOnly ? (
        <Button variant="light" color="brand" fullWidth mih={48} mb="md" leftSection={<IconPlus size={16} />} onClick={addExercise}>
          Adicionar exercício
        </Button>
      ) : null}
    </>
  );
}
