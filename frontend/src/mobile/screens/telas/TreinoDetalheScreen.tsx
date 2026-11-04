'use client';

import { Alert, Badge, Button, Collapse, Group, NumberInput, Select, SimpleGrid, Stack, Switch, Text } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { IconAlertTriangle, IconCheck, IconEdit, IconPlus, IconTrash } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useParams, useRouter } from 'next/navigation';
import { useMemo, useState } from 'react';
import { EquipmentPhoto } from '@/components/EquipmentPhoto';
import { NewWorkoutModal } from '@/components/NewWorkoutModal';
import { getAdvancedTechniqueLabel } from '@/lib/advanced-techniques';
import { apiFetch } from '@/lib/api';
import {
  describeSets,
  formatLoggedSets,
  setEditFields,
  setFormFromRow,
} from '@/lib/exercise-set-edit';
import { formatDateOrDash, formatNumberOrDash } from '@/lib/format';
import { isWorkingSetType } from '@/lib/set-types';
import { hasSides, type SideField } from '@/lib/set-sides';
import { useAdvancedTechniques } from '@/lib/use-advanced-techniques';
import { useDynamicRouteParam } from '@/lib/use-dynamic-route-param';
import {
  appendSet,
  defaultNewSet,
  dropSet,
  emptyExerciseForm,
  headerMetaPayload,
  saveExerciseLog,
  updateSetField,
} from '@/lib/exercise-log-save';
import { finishWorkoutSession } from '@/lib/workout-session-finish';
import {
  ActivityDetail,
  conductedByOptions,
  ExerciseFormState,
  ExerciseLogRow,
  extractTime,
  GymEquipmentResponse,
  MappingItem,
  MappingResponse,
  MuscleActivationResponse,
  MuscleGroupsResponse,
  sessionStatusLabels,
  WorkoutHeaderForm,
  WorkoutPlanDetail,
  WorkoutPlanExercise,
} from '@/lib/workout-log-types';
import {
  BottomSheet,
  EmptyState,
  ListBlock,
  ListGroup,
  ListRow,
  ScreenHeader,
  StatGrid,
  StatTile,
  StickyAction,
} from '../../ui';
import { PortalFieldsStyle } from './PortalFieldsStyle';
import { QueryGate, SheetButton, useBackToClose } from './common';
import { OptionPickerRow, StepperField } from './ExerciseFormParts';
import { MuscleActivationList } from './MuscleActivationList';

/**
 * Detalhe do treino (celular). Mesmas queries/chaves/payloads do
 * `WorkoutDetailClient` do desktop. O lançamento de exercício é uma visão de
 * página inteira (estado preservado ao girar/abrir teclado), não um modal.
 */
export function TreinoDetalheScreen() {
  const params = useParams<{ activityId: string }>();
  useAdvancedTechniques();
  const routeId = useDynamicRouteParam(params.activityId);
  const activityId = routeId === null ? Number.NaN : Number(routeId);
  const router = useRouter();
  const queryClient = useQueryClient();

  const [formOpen, setFormOpen] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const [discardOpen, setDiscardOpen] = useState(false);
  const [editingExerciseId, setEditingExerciseId] = useState<number | null>(null);
  const [form, setForm] = useState<ExerciseFormState>(emptyExerciseForm());
  const [selectedRow, setSelectedRow] = useState<ExerciseLogRow | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [headerEditOpen, { open: openHeaderEdit, close: closeHeaderEdit }] = useDisclosure(false);
  const [caloriesOpen, setCaloriesOpen] = useState(false);
  const [caloriesDraft, setCaloriesDraft] = useState<number | ''>('');

  const isValidId = Number.isFinite(activityId) && activityId > 0;
  // Voltar fecha a visão e MANTÉM o rascunho: reabrir continua de onde parou.
  useBackToClose(formOpen, () => setFormOpen(false));

  const activityQuery = useQuery({
    queryKey: ['activity-detail', activityId],
    queryFn: () => apiFetch<ActivityDetail>(`/activities/${activityId}`),
    enabled: isValidId,
  });
  const activity = activityQuery.data;

  const muscleActivationQuery = useQuery({
    queryKey: ['muscle-activation-by-activity', activityId],
    queryFn: () =>
      apiFetch<MuscleActivationResponse>(`/analytics/muscle-activation-by-activity?activity_id=${activityId}`),
    enabled: isValidId,
  });

  const mappingsQuery = useQuery({
    queryKey: ['mapping-catalog-active'],
    queryFn: () => apiFetch<MappingResponse>('/exercise-mapping-catalog?query=&limit=500&active_only=true'),
  });
  const selectedMap = useMemo(
    () => (mappingsQuery.data?.items || []).find((m: MappingItem) => m.exercise_name === form.exercise_name),
    [mappingsQuery.data, form.exercise_name],
  );

  const groupsQuery = useQuery({
    queryKey: ['mapping-catalog-groups'],
    queryFn: () => apiFetch<MuscleGroupsResponse>('/exercise-mapping-catalog/groups'),
  });
  const groupOptions = useMemo(
    () => (groupsQuery.data?.items || []).map((g) => ({ value: g.primary_group, label: g.primary_group })),
    [groupsQuery.data],
  );

  const gymEquipmentQuery = useQuery({
    queryKey: ['gym-equipment-options', activity?.gym_id, form.muscle_group],
    queryFn: () =>
      apiFetch<GymEquipmentResponse>(
        `/gym-equipment?gym_id=${activity?.gym_id}&primary_group=${encodeURIComponent(form.muscle_group || '')}&active_only=true`,
      ),
    enabled: Boolean(activity?.gym_id && form.muscle_group),
  });

  const equipmentOptions = useMemo(() => {
    const links = gymEquipmentQuery.data?.items || [];
    const byEquipmentId = new Map<number, (typeof links)[number]>();
    for (const link of links) {
      const existing = byEquipmentId.get(link.equipment_id);
      if (!existing) {
        byEquipmentId.set(link.equipment_id, link);
        continue;
      }
      const linkMatchesSelected = !!form.exercise_name && link.mapping_exercise_name === form.exercise_name;
      const existingMatchesSelected = !!form.exercise_name && existing.mapping_exercise_name === form.exercise_name;
      if (linkMatchesSelected && !existingMatchesSelected) byEquipmentId.set(link.equipment_id, link);
    }
    return Array.from(byEquipmentId.values()).map((link) => ({
      value: String(link.id),
      label: link.equipment_name,
      equipment_id: link.equipment_id,
      photo_url: link.photo_url ?? null,
    }));
  }, [gymEquipmentQuery.data, form.exercise_name]);

  const selectedEquipmentOption = useMemo(
    () => equipmentOptions.find((option) => option.value === form.equipment_id) || null,
    [equipmentOptions, form.equipment_id],
  );
  const selectedEquipmentLink = useMemo(
    () => (gymEquipmentQuery.data?.items || []).find((link) => String(link.id) === form.equipment_id) || null,
    [gymEquipmentQuery.data, form.equipment_id],
  );

  const exerciseOptionsForEquipment = useMemo(() => {
    if (!selectedEquipmentLink) return [];
    const links = gymEquipmentQuery.data?.items || [];
    const byName = new Map<string, (typeof links)[number]>();
    for (const link of links) {
      if (link.equipment_id !== selectedEquipmentLink.equipment_id) continue;
      if (!link.mapping_exercise_name) continue;
      if (!byName.has(link.mapping_exercise_name)) byName.set(link.mapping_exercise_name, link);
    }
    return Array.from(byName.values())
      .map((link) => ({ value: link.mapping_exercise_name as string, label: link.mapping_exercise_name as string }))
      .sort((a, b) => a.label.localeCompare(b.label));
  }, [gymEquipmentQuery.data, selectedEquipmentLink]);

  const planQuery = useQuery({
    queryKey: ['workout-plan-detail', activity?.workout_plan_id],
    queryFn: () => apiFetch<WorkoutPlanDetail>(`/workout-plans/${activity?.workout_plan_id}`),
    enabled: Boolean(activity?.workout_plan_id),
  });

  const loggedMappingIds = useMemo(
    () => new Set((activity?.exercises || []).map((ex) => ex.mapping_id).filter((id): id is number => id != null)),
    [activity?.exercises],
  );
  const loggedWorkoutPlanExerciseIds = useMemo(
    () =>
      new Set(
        (activity?.exercises || [])
          .map((ex) => ex.workout_plan_exercise_id)
          .filter((id): id is number => id != null),
      ),
    [activity?.exercises],
  );
  const planExerciseOptions = useMemo(
    () =>
      (planQuery.data?.exercises || [])
        .filter((pe) => pe.workout_plan_exercise_id != null)
        .map((pe) => ({ value: String(pe.workout_plan_exercise_id), label: `${pe.sequence}. ${pe.exercise_name}` })),
    [planQuery.data],
  );

  async function refreshActivity() {
    await queryClient.invalidateQueries({ queryKey: ['activity-detail', activityId] });
    await queryClient.invalidateQueries({ queryKey: ['activities'] });
    await queryClient.invalidateQueries({ queryKey: ['muscle-activation-by-activity', activityId] });
  }

  const saveExerciseMutation = useMutation({
    mutationFn: () => saveExerciseLog({ activityId, activity, form, editingExerciseId, selectedMap }),
    onSuccess: async () => {
      setEditingExerciseId(null);
      setForm(emptyExerciseForm());
      setFormOpen(false);
      await refreshActivity();
    },
  });

  const deleteExerciseMutation = useMutation({
    mutationFn: async (exerciseId: number) => apiFetch(`/exercises/logs/${exerciseId}`, { method: 'DELETE' }),
    onSuccess: async () => {
      if (editingExerciseId) setEditingExerciseId(null);
      closeRowSheet();
      await refreshActivity();
    },
  });

  const headerEditMutation = useMutation({
    mutationFn: async (headerForm: WorkoutHeaderForm) =>
      apiFetch(`/workout-sessions/${activityId}/meta`, {
        method: 'PATCH',
        body: JSON.stringify(headerMetaPayload(headerForm)),
      }),
    onSuccess: async () => {
      closeHeaderEdit();
      await refreshActivity();
    },
  });

  const caloriesMutation = useMutation({
    mutationFn: async (kcal: number | null) =>
      apiFetch(`/workout-sessions/${activityId}/meta`, {
        method: 'PATCH',
        body: JSON.stringify({ calories_kcal: kcal }),
      }),
    onSuccess: async () => {
      await refreshActivity();
    },
  });

  const finishMutation = useMutation({
    mutationFn: async () => finishWorkoutSession(activityId),
    onSuccess: async () => {
      await refreshActivity();
    },
  });

  function closeRowSheet() {
    setSelectedRow(null);
    setConfirmDelete(false);
    deleteExerciseMutation.reset();
  }

  function addSet() {
    setForm((current) => ({ ...current, sets: appendSet(current.sets) }));
  }

  function removeSet(index: number) {
    setForm((current) => ({ ...current, sets: dropSet(current.sets, index) }));
  }

  function updateSet(
    index: number,
    field: 'reps' | 'weight_kg' | 'rpe' | 'duration_seconds' | SideField,
    value: number | null,
  ) {
    setForm((current) => ({ ...current, sets: updateSetField(current.sets, index, field, value) }));
  }

  function loadForEdit(row: ExerciseLogRow) {
    setEditingExerciseId(row.id);
    setForm({
      exercise_name: row.original_name,
      equipment_id: row.equipment_id ? String(row.equipment_id) : '',
      muscle_group: row.muscle_primary ?? null,
      workout_plan_exercise_id: row.workout_plan_exercise_id ? String(row.workout_plan_exercise_id) : '',
      muscle_pain: row.muscle_pain ?? null,
      joint_pain: Boolean(row.joint_pain),
      sets: row.sets.map(setFormFromRow),
    });
    closeRowSheet();
    saveExerciseMutation.reset();
    setFormOpen(true);
  }

  function startNewExercise() {
    // Rascunho em andamento (de um lançamento novo) continua; saindo de uma edição, começa limpo.
    if (editingExerciseId !== null) {
      setEditingExerciseId(null);
      setForm(emptyExerciseForm());
    }
    saveExerciseMutation.reset();
    setFormOpen(true);
  }

  function resetForm() {
    setEditingExerciseId(null);
    setForm(emptyExerciseForm());
  }

  function prefillFromPlanExercise(planExercise: WorkoutPlanExercise) {
    const matched =
      (mappingsQuery.data?.items || []).find((m) => m.id === planExercise.mapping_id) ||
      (mappingsQuery.data?.items || []).find((m) => m.exercise_name === planExercise.exercise_name);
    setEditingExerciseId(null);
    setForm({
      exercise_name: matched?.exercise_name || planExercise.exercise_name,
      equipment_id: planExercise.preferred_equipment_id ? String(planExercise.preferred_equipment_id) : '',
      muscle_group: matched?.primary_group ?? planExercise.muscle_primary ?? null,
      workout_plan_exercise_id:
        planExercise.workout_plan_exercise_id != null ? String(planExercise.workout_plan_exercise_id) : '',
      muscle_pain: null,
      joint_pain: false,
      sets: [defaultNewSet()],
    });
    saveExerciseMutation.reset();
    setFormOpen(true);
  }

  async function saveCalories() {
    const kcal = activity?.calories_kcal == null ? null : Number(activity.calories_kcal);
    const shown: number | '' = kcal != null && Number.isFinite(kcal) ? Math.round(kcal) : '';
    // Nada mudou: fecha sem PATCH (gravar o arredondado trocaria a origem por "Manual").
    if (caloriesDraft === shown) {
      setCaloriesOpen(false);
      return;
    }
    try {
      await caloriesMutation.mutateAsync(caloriesDraft === '' ? null : caloriesDraft);
      setCaloriesOpen(false);
    } catch {
      // o erro aparece na folha; ela fica aberta para corrigir.
    }
  }

  function openCalories() {
    const kcal = activity?.calories_kcal == null ? null : Number(activity.calories_kcal);
    setCaloriesDraft(kcal != null && Number.isFinite(kcal) ? Math.round(kcal) : '');
    caloriesMutation.reset();
    setCaloriesOpen(true);
  }

  if (!isValidId) {
    return (
      <>
        <ScreenHeader title="Treino" />
        <Alert color="red" icon={<IconAlertTriangle size={16} />}>
          Identificador de treino inválido.
        </Alert>
        <Button variant="default" fullWidth mt="md" mih={48} onClick={() => router.push('/registro-exercicios')}>
          Voltar aos treinos
        </Button>
      </>
    );
  }

  if (!activity) {
    return (
      <>
        <ScreenHeader title="Treino" />
        <QueryGate
          isLoading={activityQuery.isLoading}
          isError={activityQuery.isError || !activityQuery.isLoading}
          error={activityQuery.error ?? new Error('Treino não encontrado.')}
          onRetry={() => void activityQuery.refetch()}
          loadingLabel="Carregando treino..."
          errorTitle="Treino não encontrado"
        >
          {null}
        </QueryGate>
      </>
    );
  }

  const canFinish = activity.session_status !== 'done';
  const statusInfo = activity.session_status ? sessionStatusLabels[activity.session_status] : null;
  const headerInitialValues: WorkoutHeaderForm = {
    name: activity.name,
    local_date: activity.local_date,
    started_at_time: extractTime(activity.started_at),
    gym_id: activity.gym_id ? String(activity.gym_id) : '',
    training_type_id: activity.training_type_id ? String(activity.training_type_id) : '',
    conducted_by: activity.conducted_by || '',
    workout_plan_id: activity.workout_plan_id ? String(activity.workout_plan_id) : '',
  };
  const formSetDisplay = describeSets(form.sets);
  const kcal = activity.calories_kcal == null ? null : Number(activity.calories_kcal);
  const when = [
    formatDateOrDash(activity.local_date) + (extractTime(activity.started_at) ? ` às ${extractTime(activity.started_at)}` : ''),
    activity.gym_name,
    activity.conducted_by
      ? (conductedByOptions.find((o) => o.value === activity.conducted_by)?.label || activity.conducted_by)
      : null,
  ]
    .filter(Boolean)
    .join(' · ');

  // ---- Visão do formulário de lançamento/edição (página inteira) ----
  if (formOpen) {
    return (
      <>
        <ScreenHeader
          title={editingExerciseId ? 'Editar exercício' : 'Lançar exercício'}
          subtitle={editingExerciseId ? `Editando ID ${editingExerciseId}` : activity.name}
        />
        <Stack gap="md" pb="md">
          <ListGroup title="Exercício">
            <OptionPickerRow
              label="Grupo muscular"
              placeholder="Selecione o grupo"
              options={groupOptions}
              value={form.muscle_group}
              onSelect={(value) => setForm((c) => ({ ...c, muscle_group: value || null, equipment_id: '', exercise_name: '' }))}
              emptyText="Nenhum grupo muscular encontrado"
            />
            <OptionPickerRow
              label="Equipamento"
              placeholder={
                !activity.gym_id ? 'Sem academia definida' : !form.muscle_group ? 'Escolha o grupo primeiro' : 'Selecione o equipamento'
              }
              options={equipmentOptions}
              value={form.equipment_id || null}
              onSelect={(value) => setForm((c) => ({ ...c, equipment_id: value || '', exercise_name: '' }))}
              disabled={!activity.gym_id || !form.muscle_group}
              clearLabel="Nenhum equipamento"
              emptyText="Nenhum equipamento cadastrado para esse grupo muscular nessa academia"
            />
            <OptionPickerRow
              label="Exercício canônico"
              placeholder={selectedEquipmentLink ? 'Selecione o exercício' : 'Escolha o equipamento primeiro'}
              options={exerciseOptionsForEquipment}
              value={form.exercise_name || null}
              disabled={!selectedEquipmentLink}
              onSelect={(value) => {
                if (!value || !selectedEquipmentLink) return;
                const exactLink = (gymEquipmentQuery.data?.items || []).find(
                  (link) => link.equipment_id === selectedEquipmentLink.equipment_id && link.mapping_exercise_name === value,
                );
                setForm((c) => ({
                  ...c,
                  exercise_name: value,
                  equipment_id: exactLink ? String(exactLink.id) : c.equipment_id,
                }));
              }}
              emptyText="Nenhum exercício encontrado para este equipamento"
            />
            {activity.workout_plan_id && planQuery.data ? (
              <OptionPickerRow
                label="Associar a exercício da ficha"
                placeholder="Nenhum (exercício avulso)"
                options={planExerciseOptions}
                value={form.workout_plan_exercise_id || null}
                onSelect={(value) => setForm((c) => ({ ...c, workout_plan_exercise_id: value || '' }))}
                clearLabel="Nenhum (exercício avulso)"
                emptyText="Nenhum exercício encontrado na ficha"
              />
            ) : null}
          </ListGroup>

          <Text fw={600}>Séries</Text>
          {form.sets.map((set, index) => {
            const display = formSetDisplay[index];
            const fields = setEditFields(set.measure);
            return (
              <ListGroup key={`${set.set_number}-${index}`} aria-label={`Série ${index + 1}`}>
                <ListBlock>
                  <Group justify="space-between" mb="xs" wrap="nowrap">
                    <Stack gap={2}>
                      <Badge variant="light" color={display?.isApplication ? 'green' : display != null && isWorkingSetType(display.setType) ? 'brand' : 'gray'}>
                        {display?.isApplication ? '↳ ' : ''}
                        {display?.label ?? `Série ${index + 1}`}
                      </Badge>
                      {display?.isApplication ? (
                        <Text size="xs" c="dimmed">
                          {getAdvancedTechniqueLabel(set.technique_code) ?? 'Técnica'}
                          {display.anchorLabel ? ` · âncora: ${display.anchorLabel}` : ''}
                        </Text>
                      ) : null}
                    </Stack>
                    <Button
                      variant="subtle"
                      color="red"
                      mih={44}
                      leftSection={<IconTrash size={16} />}
                      onClick={() => removeSet(index)}
                      disabled={form.sets.length <= 1}
                      aria-label={`Remover série ${index + 1}`}
                    >
                      Remover
                    </Button>
                  </Group>
                  {hasSides(set) && fields.showReps ? (
                    <>
                      <StepperField label="Reps E" min={0} max={100} step={1} value={set.reps_left} onChange={(v) => updateSet(index, 'reps_left', v)} />
                      <StepperField label="Reps D" min={0} max={100} step={1} value={set.reps_right} onChange={(v) => updateSet(index, 'reps_right', v)} />
                    </>
                  ) : null}
                  {!hasSides(set) && fields.showReps ? (
                    <StepperField label="Repetições" min={fields.repsMin ?? 1} max={100} step={1} value={set.reps} onChange={(v) => updateSet(index, 'reps', v)} />
                  ) : null}
                  {fields.showDuration ? (
                    <StepperField label="Tempo (s)" min={fields.durationMin} max={fields.durationMax} step={1} value={set.duration_seconds} onChange={(v) => updateSet(index, 'duration_seconds', v)} />
                  ) : null}
                  <StepperField label="Carga (kg)" inputMode="decimal" min={0} max={1000} step={0.5} decimalScale={2} value={set.weight_kg} onChange={(v) => updateSet(index, 'weight_kg', Number(v ?? 0))} />
                  {hasSides(set) ? (
                    <>
                      <StepperField stepper={false} label="RPE E" inputMode="decimal" min={0} max={10} step={0.5} decimalScale={1} value={set.rpe_left} onChange={(v) => updateSet(index, 'rpe_left', v)} />
                      <StepperField stepper={false} label="RPE D" inputMode="decimal" min={0} max={10} step={0.5} decimalScale={1} value={set.rpe_right} onChange={(v) => updateSet(index, 'rpe_right', v)} />
                    </>
                  ) : (
                    <StepperField stepper={false} label="RPE" inputMode="decimal" min={0} max={10} step={0.5} decimalScale={1} value={set.rpe} onChange={(v) => updateSet(index, 'rpe', v)} />
                  )}
                </ListBlock>
              </ListGroup>
            );
          })}
          <Button variant="default" mih={48} leftSection={<IconPlus size={16} />} onClick={addSet}>
            Adicionar série
          </Button>
          <Button variant="subtle" color="gray" mih={44} onClick={() => setMoreOpen((v) => !v)} aria-expanded={moreOpen}>
            {moreOpen ? 'Ocultar detalhes' : 'Mais detalhes (dor muscular e articular)'}
          </Button>
          <Collapse in={moreOpen} transitionDuration={0}>
            <Stack gap="md">
          <NumberInput
            size="md"
            label="Dor muscular (0-10)"
            description="Opcional: dor muscular residual sentida neste exercício"
            min={0}
            max={10}
            step={1}
            inputMode="numeric"
            hideControls
            value={form.muscle_pain ?? undefined}
            onChange={(value) => setForm((c) => ({ ...c, muscle_pain: value == null || value === '' ? null : Number(value) }))}
            clampBehavior="strict"
          />
          <Switch
            size="md"
            label="Dor articular"
            description="Opcional: reportou dor articular neste exercício"
            checked={form.joint_pain}
            onChange={(event) => {
              const checked = event.currentTarget.checked;
              setForm((c) => ({ ...c, joint_pain: checked }));
            }}
          />
            </Stack>
          </Collapse>

          <ListGroup>
            <ListRow destructive title="Descartar rascunho" onClick={() => setDiscardOpen(true)} />
          </ListGroup>
          {saveExerciseMutation.isError ? (
            <Alert color="red" icon={<IconAlertTriangle size={16} />} role="alert">
              {saveExerciseMutation.error instanceof Error ? saveExerciseMutation.error.message : String(saveExerciseMutation.error)}
            </Alert>
          ) : null}
        </Stack>
        <BottomSheet
          opened={discardOpen}
          onClose={() => setDiscardOpen(false)}
          title="Descartar rascunho?"
          footer={
            <>
              <SheetButton variant="default" onClick={() => setDiscardOpen(false)}>
                Continuar editando
              </SheetButton>
              <SheetButton
                color="red"
                onClick={() => {
                  setDiscardOpen(false);
                  resetForm();
                  setFormOpen(false);
                }}
              >
                Descartar
              </SheetButton>
            </>
          }
        >
          <Text>O que você digitou neste lançamento será apagado.</Text>
        </BottomSheet>
        <StickyAction>
          <Button
            fullWidth
            size="lg"
            color="brand"
            mih={48}
            loading={saveExerciseMutation.isPending}
            onClick={() => saveExerciseMutation.mutate()}
          >
            {editingExerciseId ? 'Salvar alterações' : 'Lançar exercício'}
          </Button>
        </StickyAction>
      </>
    );
  }

  // ---- Visão do detalhe ----
  return (
    <>
      <ScreenHeader title={activity.name} subtitle={when} />
      <Group gap="xs" mb="md">
        {statusInfo ? (
          <Badge color={statusInfo.color} variant="light">
            {statusInfo.label}
          </Badge>
        ) : null}
        {activity.workout_plan_name ? (
          <Badge color="grape" variant="light">
            Ficha: {activity.workout_plan_name}
          </Badge>
        ) : null}
      </Group>

      <StatGrid>
        <StatTile label="Carga total" value={`${formatNumberOrDash(activity.total_volume_kg, 1)} kg`} />
        <StatTile label="RPE médio" value={formatNumberOrDash(activity.avg_rpe, 1)} />
        <StatTile label="Exercícios" value={String(activity.exercises.length)} />
        <StatTile
          label="kcal gastas"
          value={formatNumberOrDash(kcal, 0)}
        />
      </StatGrid>

      <ListGroup title="Treino">
        <ListRow icon={IconEdit} title="Editar treino" onClick={openHeaderEdit} />
        <ListRow icon={IconEdit} title="Editar kcal gastas" subtitle={activity.calories_source_label ?? undefined} onClick={openCalories} />
        {canFinish ? (
          <ListRow
            icon={IconCheck}
            title="Finalizar treino"
            subtitle={finishMutation.isError ? String(finishMutation.error) : finishMutation.isPending ? 'Finalizando...' : undefined}
            onClick={finishMutation.isPending ? undefined : () => finishMutation.mutate()}
          />
        ) : null}
      </ListGroup>

      {activity.ai_summary ? (
        <ListGroup
          title="Resumo do treino (IA)"
          footer={
            activity.ai_summary_generated_at
              ? `Gerado em ${new Date(activity.ai_summary_generated_at).toLocaleString('pt-BR')}`
              : undefined
          }
        >
          <ListBlock>
            <Text size="sm">{activity.ai_summary}</Text>
          </ListBlock>
        </ListGroup>
      ) : null}

      {activity.workout_plan_id && planQuery.data ? (
        <ListGroup title="Exercícios da ficha vinculada">
          {planQuery.data.exercises.map((pe) => {
            const already =
              pe.workout_plan_exercise_id != null
                ? loggedWorkoutPlanExerciseIds.has(pe.workout_plan_exercise_id)
                : pe.mapping_id != null && loggedMappingIds.has(pe.mapping_id);
            return (
              <ListRow
                key={pe.workout_plan_exercise_id ?? pe.sequence}
                title={`${pe.sequence}. ${pe.exercise_name}`}
                subtitle={[pe.sets_count ? `${pe.sets_count}x${pe.reps_target || ''}` : null, already ? 'Já lançado' : 'Toque para lançar']
                  .filter(Boolean)
                  .join(' · ')}
                onClick={() => prefillFromPlanExercise(pe)}
              />
            );
          })}
        </ListGroup>
      ) : null}

      <ListGroup title="Exercícios lançados" footer={`${activity.exercises.length} itens`}>
        {activity.exercises.length === 0 ? (
          <ListBlock>
            <EmptyState title="Nenhum exercício lançado neste treino ainda." />
          </ListBlock>
        ) : (
          activity.exercises.map((row) => {
            const swapped =
              row.workout_plan_exercise_id != null &&
              row.planned_mapping_id != null &&
              row.planned_mapping_id !== row.mapping_id;
            return (
              <ListRow
                key={row.id}
                title={row.original_name}
                subtitle={[
                  row.equipment_name,
                  swapped ? `Trocado (planejado: ${row.planned_exercise_name || 'exercício da ficha'})` : null,
                ]
                  .filter(Boolean)
                  .join(' · ')}
                value={`${row.sets.length} ${row.sets.length === 1 ? 'série' : 'séries'}`}
                onClick={() => setSelectedRow(row)}
              />
            );
          })
        )}
      </ListGroup>

      <MuscleActivationList
        rows={muscleActivationQuery.data?.items || []}
        isLoading={muscleActivationQuery.isLoading}
        isError={muscleActivationQuery.isError}
      />

      <StickyAction>
        <Button fullWidth size="lg" color="brand" mih={48} leftSection={<IconPlus size={20} />} onClick={startNewExercise}>
          Lançar exercício
        </Button>
      </StickyAction>

      <BottomSheet
        opened={selectedRow !== null}
        onClose={closeRowSheet}
        title={confirmDelete ? 'Confirmar exclusão' : (selectedRow?.original_name ?? 'Exercício')}
        footer={
          selectedRow ? (
            confirmDelete ? (
              <>
                <SheetButton variant="default" disabled={deleteExerciseMutation.isPending} onClick={() => setConfirmDelete(false)}>
                  Cancelar
                </SheetButton>
                <SheetButton
                  color="red"
                  loading={deleteExerciseMutation.isPending}
                  onClick={() => deleteExerciseMutation.mutate(selectedRow.id)}
                >
                  Apagar
                </SheetButton>
              </>
            ) : (
              <>
                <SheetButton variant="default" color="red" onClick={() => setConfirmDelete(true)}>
                  Excluir
                </SheetButton>
                <SheetButton color="brand" onClick={() => loadForEdit(selectedRow)}>
                  Editar
                </SheetButton>
              </>
            )
          ) : undefined
        }
      >
        {selectedRow && confirmDelete ? (
          <Text>Deseja apagar este exercício do treino?</Text>
        ) : selectedRow ? (
          <ListGroup>
            <ListRow title="Amazfit" value={selectedRow.mapped_name || '-'} />
            <ListRow
              title="Equipamento"
              value={selectedRow.equipment_name || '-'}
              trailing={
                <EquipmentPhoto photoUrl={selectedRow.equipment_photo_url} alt={selectedRow.equipment_name || 'Equipamento'} size={40} />
              }
            />
            <ListRow title="Séries" subtitle={formatLoggedSets(selectedRow.sets)} />
          </ListGroup>
        ) : null}
      </BottomSheet>

      <BottomSheet
        opened={caloriesOpen}
        onClose={() => setCaloriesOpen(false)}
        title="kcal gastas"
        footer={
          <>
            <SheetButton variant="default" disabled={caloriesMutation.isPending} onClick={() => setCaloriesOpen(false)}>
              Cancelar
            </SheetButton>
            <SheetButton color="brand" loading={caloriesMutation.isPending} onClick={() => void saveCalories()}>
              Salvar
            </SheetButton>
          </>
        }
      >
        <NumberInput
          size="md"
          label="kcal gastas"
          inputMode="numeric"
          min={0}
          decimalScale={0}
          value={caloriesDraft}
          onChange={(value) => setCaloriesDraft(value === '' ? '' : Number(value))}
          description="Vazio limpa o valor"
        />
        {caloriesMutation.isError ? (
          <Text size="sm" c="red" role="alert">
            {String(caloriesMutation.error)}
          </Text>
        ) : null}
      </BottomSheet>

      <PortalFieldsStyle />
      <NewWorkoutModal
        opened={headerEditOpen}
        onClose={closeHeaderEdit}
        title="Editar treino"
        submitLabel="Salvar alterações"
        initialValues={headerInitialValues}
        onSubmit={(headerForm) => headerEditMutation.mutateAsync(headerForm).then(() => undefined)}
        isSubmitting={headerEditMutation.isPending}
        errorMessage={headerEditMutation.isError ? String(headerEditMutation.error) : null}
      />
    </>
  );
}
