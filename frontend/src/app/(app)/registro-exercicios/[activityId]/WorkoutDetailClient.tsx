'use client';

import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Group,
  Loader,
  Modal,
  NumberInput,
  Paper,
  Select,
  Stack,
  Switch,
  Table,
  Text,
  Title,
  Tooltip,
} from '@mantine/core';
import {
  IconAlertTriangle,
  IconArrowLeft,
  IconCheck,
  IconEdit,
  IconExchange,
  IconPlus,
  IconSparkles,
  IconTrash,
} from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useDisclosure, useMediaQuery } from '@mantine/hooks';
import { useParams, useRouter } from 'next/navigation';
import { useMemo, useState } from 'react';
import { apiFetch } from '@/lib/api';
import {
  describeSets,
  formatLoggedSets,
  newMeasuredSetFromLast,
  setEditFields,
  setFormFromRow,
  setUpdatePayload,
} from '@/lib/exercise-set-edit';
import { getAdvancedTechniqueLabel } from '@/lib/advanced-techniques';
import { hasSides, type SideField } from '@/lib/set-sides';
import { useAdvancedTechniques } from '@/lib/use-advanced-techniques';
import { finishWorkoutSession } from '@/lib/workout-session-finish';
import { useDynamicRouteParam } from '@/lib/use-dynamic-route-param';
import { EquipmentPhoto } from '@/components/EquipmentPhoto';
import { MuscleActivationTable } from '@/components/MuscleActivationTable';
import { NewWorkoutModal } from '@/components/NewWorkoutModal';
import {
  ActivityDetail,
  buildStartedAt,
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
import { isWorkingSetType } from '@/lib/set-types';
import { BRAND } from '@/theme';
import { formatNumberOrDash } from '@/lib/format';

function emptyExerciseForm(): ExerciseFormState {
  return {
    exercise_name: '',
    equipment_id: '',
    muscle_group: null,
    workout_plan_exercise_id: '',
    muscle_pain: null,
    joint_pain: false,
    sets: [{ set_number: 1, reps: 10, weight_kg: 20, rpe: null }],
  };
}

type StatusCardProps = {
  activity: ActivityDetail;
  /** PATCH /workout-sessions/{id}/meta com `calories_kcal` (null limpa). */
  onSaveCalories: (kcal: number | null) => Promise<unknown>;
  savingCalories: boolean;
  caloriesError: string | null;
};

// kcal gastas do treino (nutrição L0): valor + selo da origem resolvida pelo
// backend (Intervals / manual) + edição inline pelo dono. O Intervals
// preenche sozinho no dia seguinte; a edição manual é a exceção.
function CaloriesRow({
  activity,
  onSaveCalories,
  savingCalories,
  caloriesError,
}: StatusCardProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<number | ''>('');
  const kcal = activity.calories_kcal == null ? null : Number(activity.calories_kcal);
  // Valor exibido (e ponto de partida da edição), sem casas decimais.
  const shown: number | '' = kcal != null && Number.isFinite(kcal) ? Math.round(kcal) : '';

  function startEdit() {
    setDraft(shown);
    setEditing(true);
  }

  async function save() {
    // Nada mudou: fecha sem PATCH. Gravar o valor arredondado da tela trocaria
    // a kcal do Intervals (ex.: 412,37) por 412 e a origem por "Manual".
    if (draft === shown) {
      setEditing(false);
      return;
    }
    try {
      await onSaveCalories(draft === '' ? null : draft);
      setEditing(false);
    } catch {
      // O erro já aparece em `caloriesError`; a edição fica aberta para corrigir.
    }
  }

  if (editing) {
    return (
      <Group gap="xs" wrap="nowrap" align="flex-end">
        <NumberInput
          size="xs"
          w={120}
          label="kcal gastas"
          min={0}
          decimalScale={0}
          value={draft}
          onChange={(value) => setDraft(value === '' ? '' : Number(value))}
          description="Vazio limpa o valor"
        />
        <Button size="xs" loading={savingCalories} onClick={() => void save()}>
          Salvar
        </Button>
        <Button size="xs" variant="subtle" disabled={savingCalories} onClick={() => setEditing(false)}>
          Cancelar
        </Button>
        {caloriesError && (
          <Text size="xs" c="red">
            {caloriesError}
          </Text>
        )}
      </Group>
    );
  }

  return (
    <Group gap={6} wrap="nowrap">
      <Text size="sm" c={BRAND.textMuted}>
        <Text span fw={600} c={BRAND.textPrimary}>
          kcal gastas:{' '}
        </Text>
        {formatNumberOrDash(kcal, 0)}
      </Text>
      {activity.calories_source_label && (
        <Badge size="xs" variant="light" color="gray">
          {activity.calories_source_label}
        </Badge>
      )}
      <Tooltip label="Editar kcal gastas" withArrow>
        <ActionIcon size="sm" variant="subtle" color="gray" aria-label="Editar kcal gastas" onClick={startEdit}>
          <IconEdit size={14} />
        </ActionIcon>
      </Tooltip>
    </Group>
  );
}

function ActivitySummary(props: StatusCardProps) {
  const { activity } = props;
  const statusInfo = activity.session_status ? sessionStatusLabels[activity.session_status] : null;
  return (
    <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
      <Group justify="space-between" align="flex-start" mb="sm">
        <div>
          <Title order={2} c={BRAND.textPrimary}>
            {activity.name}
          </Title>
          <Text c={BRAND.textMuted}>
            {activity.local_date}
            {extractTime(activity.started_at) ? ` às ${extractTime(activity.started_at)}` : ''}
            {activity.gym_name ? ` · ${activity.gym_name}` : ''}
            {activity.conducted_by
              ? ` · ${conductedByOptions.find((o) => o.value === activity.conducted_by)?.label || activity.conducted_by}`
              : ''}
          </Text>
        </div>
        <Group gap="xs">
          {statusInfo && (
            <Badge color={statusInfo.color} variant="light">
              {statusInfo.label}
            </Badge>
          )}
          {activity.workout_plan_name && (
            <Badge color="grape" variant="light">
              Ficha: {activity.workout_plan_name}
            </Badge>
          )}
        </Group>
      </Group>

      <Group gap="xl">
        <Text size="sm" c={BRAND.textMuted}>
          <Text span fw={600} c={BRAND.textPrimary}>
            Carga total:{' '}
          </Text>
          {formatNumberOrDash(activity.total_volume_kg, 1)} kg
        </Text>
        <Text size="sm" c={BRAND.textMuted}>
          <Text span fw={600} c={BRAND.textPrimary}>
            RPE médio:{' '}
          </Text>
          {formatNumberOrDash(activity.avg_rpe, 1)}
        </Text>
        <Text size="sm" c={BRAND.textMuted}>
          <Text span fw={600} c={BRAND.textPrimary}>
            Exercícios:{' '}
          </Text>
          {activity.exercises.length}
        </Text>
        <CaloriesRow {...props} />
      </Group>
    </Paper>
  );
}

// Client Component com todo o detalhe/edição do treino — separado de
// `page.tsx` (Server Component) porque `output: 'export'` proíbe
// `generateStaticParams` em um arquivo com `'use client'` (ver page.tsx).
export function WorkoutDetailClient() {
  const params = useParams<{ activityId: string }>();
  // Re-renderiza os rótulos de técnica quando o catálogo chega da API.
  useAdvancedTechniques();
  // Em `output: 'export'` o `useParams()` devolve o slug do build
  // ("placeholder"), não o id da barra de endereços — ver
  // `useDynamicRouteParam`. `Number(null)` seria 0, então o ternário força
  // NaN enquanto o id não foi resolvido (o guard `isValidId` abaixo cobre).
  const routeId = useDynamicRouteParam(params.activityId);
  const activityId = routeId === null ? Number.NaN : Number(routeId);
  const router = useRouter();
  const queryClient = useQueryClient();
  const isMobile = useMediaQuery('(max-width: 48em)');

  const [editingExerciseId, setEditingExerciseId] = useState<number | null>(null);
  const [form, setForm] = useState<ExerciseFormState>(emptyExerciseForm());
  const [deleteTarget, setDeleteTarget] = useState<ExerciseLogRow | null>(null);
  const [deleteOpen, { open: openDelete, close: closeDelete }] = useDisclosure(false);
  const [headerEditOpen, { open: openHeaderEdit, close: closeHeaderEdit }] = useDisclosure(false);

  const isValidId = Number.isFinite(activityId) && activityId > 0;

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

  // M76 E4: cada linha já traz `assessment` (status e alvo) do backend.
  const muscleActivationRows = muscleActivationQuery.data?.items || [];

  const mappingsQuery = useQuery({
    queryKey: ['mapping-catalog-active'],
    queryFn: () => apiFetch<MappingResponse>('/exercise-mapping-catalog?query=&limit=500&active_only=true'),
  });

  const selectedMap = useMemo(
    () => (mappingsQuery.data?.items || []).find((m: MappingItem) => m.exercise_name === form.exercise_name),
    [mappingsQuery.data, form.exercise_name],
  );

  // Passo 1: grupo muscular, escolhido explicitamente, para então filtrar
  // equipamento e, por fim, o exercício.
  const groupsQuery = useQuery({
    queryKey: ['mapping-catalog-groups'],
    queryFn: () => apiFetch<MuscleGroupsResponse>('/exercise-mapping-catalog/groups'),
  });

  const groupOptions = useMemo(
    () => (groupsQuery.data?.items || []).map((g) => ({ value: g.primary_group, label: g.primary_group })),
    [groupsQuery.data],
  );

  // Passo 2: equipamento desta academia que atende o grupo escolhido.
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
      if (linkMatchesSelected && !existingMatchesSelected) {
        byEquipmentId.set(link.equipment_id, link);
      }
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

  // Passo 3: exercícios que este equipamento (dentro do grupo escolhido) atende.
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
        .map((pe) => ({
          value: String(pe.workout_plan_exercise_id),
          label: `${pe.sequence}. ${pe.exercise_name}`,
        })),
    [planQuery.data],
  );

  async function refreshActivity() {
    await queryClient.invalidateQueries({ queryKey: ['activity-detail', activityId] });
    await queryClient.invalidateQueries({ queryKey: ['activities'] });
    await queryClient.invalidateQueries({ queryKey: ['muscle-activation-by-activity', activityId] });
  }

  const saveExerciseMutation = useMutation({
    mutationFn: async () => {
      if (!form.exercise_name) {
        throw new Error('Selecione um exercício.');
      }

      if (editingExerciseId) {
        return apiFetch(`/exercises/logs/${editingExerciseId}`, {
          method: 'PUT',
          body: JSON.stringify({
            local_date: activity?.local_date,
            started_at: activity?.started_at || null,
            original_name: form.exercise_name,
            mapping_id: selectedMap?.id ?? null,
            mapped_name: selectedMap?.amazfit_name || form.exercise_name,
            muscle_primary: selectedMap?.primary_group || null,
            muscle_secondary: selectedMap?.secondary_group || null,
            gym_id: activity?.gym_id ?? null,
            conducted_by: activity?.conducted_by ?? null,
            equipment_id: form.equipment_id ? Number(form.equipment_id) : null,
            workout_plan_exercise_id: form.workout_plan_exercise_id ? Number(form.workout_plan_exercise_id) : null,
            muscle_pain: form.muscle_pain,
            joint_pain: form.joint_pain,
            sets: form.sets.map(setUpdatePayload),
          }),
        });
      }

      const exerciseResult = await apiFetch<{ exercise_id: number }>(`/workout-sessions/${activityId}/exercises`, {
        method: 'POST',
        body: JSON.stringify({
          original_name: form.exercise_name,
          mapping_id: selectedMap?.id ?? null,
          equipment_id: form.equipment_id ? Number(form.equipment_id) : null,
          workout_plan_exercise_id: form.workout_plan_exercise_id ? Number(form.workout_plan_exercise_id) : null,
          muscle_pain: form.muscle_pain,
          joint_pain: form.joint_pain,
        }),
      });

      for (let index = 0; index < form.sets.length; index += 1) {
        const set = form.sets[index];
        try {
          await apiFetch(`/workout-sessions/${activityId}/exercises/${exerciseResult.exercise_id}/sets`, {
            method: 'POST',
            body: JSON.stringify({
              set_number: index + 1,
              reps: set.reps,
              weight_kg: set.weight_kg,
              rpe: set.rpe,
            }),
          });
        } catch (error) {
          throw new Error(
            `Exercício criado (série ${index + 1} de ${form.sets.length} falhou ao salvar). ` +
              `Edite o registro na lista abaixo para completar as séries restantes. Detalhe: ${
                error instanceof Error ? error.message : String(error)
              }`,
          );
        }
      }

      return exerciseResult;
    },
    onSuccess: async () => {
      setEditingExerciseId(null);
      setForm(emptyExerciseForm());
      await refreshActivity();
    },
  });

  const deleteExerciseMutation = useMutation({
    mutationFn: async (exerciseId: number) => apiFetch(`/exercises/logs/${exerciseId}`, { method: 'DELETE' }),
    onSuccess: async () => {
      if (editingExerciseId) setEditingExerciseId(null);
      closeDelete();
      setDeleteTarget(null);
      await refreshActivity();
    },
  });

  const headerEditMutation = useMutation({
    mutationFn: async (headerForm: WorkoutHeaderForm) =>
      apiFetch(`/workout-sessions/${activityId}/meta`, {
        method: 'PATCH',
        body: JSON.stringify({
          name: headerForm.name,
          local_date: headerForm.local_date,
          started_at: buildStartedAt(headerForm.local_date, headerForm.started_at_time),
          gym_id: headerForm.gym_id ? Number(headerForm.gym_id) : null,
          training_type_id: headerForm.training_type_id ? Number(headerForm.training_type_id) : null,
          conducted_by: headerForm.conducted_by || null,
          workout_plan_id: headerForm.workout_plan_id ? Number(headerForm.workout_plan_id) : null,
        }),
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

  function addSet() {
    setForm((current) => ({
      ...current,
      sets: [
        ...current.sets,
        newMeasuredSetFromLast(current.sets[current.sets.length - 1], current.sets.length + 1) ?? {
          set_number: current.sets.length + 1,
          reps: 10,
          weight_kg: 20,
          rpe: null,
        },
      ],
    }));
  }

  function removeSet(index: number) {
    setForm((current) => {
      if (current.sets.length <= 1) return current;
      const nextSets = current.sets.filter((_, i) => i !== index).map((set, i) => ({ ...set, set_number: i + 1 }));
      return { ...current, sets: nextSets };
    });
  }

  function updateSet(
    index: number,
    field: 'reps' | 'weight_kg' | 'rpe' | 'duration_seconds' | SideField,
    value: number | null,
  ) {
    setForm((current) => ({
      ...current,
      sets: current.sets.map((set, i) => {
        if (i !== index) return set;
        // M102: lado vazio fica vazio (o servidor ignora o lado vazio).
        if (field === 'rpe' || field === 'duration_seconds' || field.endsWith('_left') || field.endsWith('_right')) {
          return { ...set, [field]: value == null || Number.isNaN(Number(value)) ? null : Number(value) };
        }
        // Máximo de reps (Lote T5): vazio fica vazio (o piso é do dono), não 0.
        if (field === 'reps' && setEditFields(set.measure).repsMin != null && (value == null || Number.isNaN(Number(value)))) {
          return { ...set, reps: null };
        }
        return { ...set, [field]: Number(value) || 0 };
      }),
    }));
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
  }

  function resetForm() {
    setEditingExerciseId(null);
    setForm(emptyExerciseForm());
  }

  function openDeleteConfirm(row: ExerciseLogRow) {
    setDeleteTarget(row);
    openDelete();
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
      sets: [{ set_number: 1, reps: 10, weight_kg: 20, rpe: null }],
    });
  }

  if (!isValidId) {
    return (
      <Stack gap="md">
        <Alert color="red" icon={<IconAlertTriangle size={16} />}>
          Identificador de treino inválido.
        </Alert>
        <Button
          variant="default"
          leftSection={<IconArrowLeft size={16} />}
          onClick={() => router.push('/registro-exercicios')}
        >
          Voltar
        </Button>
      </Stack>
    );
  }

  if (activityQuery.isLoading) {
    return (
      <Group>
        <Loader size="sm" color="brand" />
        <Text c={BRAND.textMuted}>Carregando treino...</Text>
      </Group>
    );
  }

  if (activityQuery.isError || !activity) {
    return (
      <Stack gap="md">
        <Alert color="red" icon={<IconAlertTriangle size={16} />}>
          {activityQuery.error instanceof Error ? activityQuery.error.message : 'Treino não encontrado.'}
        </Alert>
        <Button
          variant="default"
          leftSection={<IconArrowLeft size={16} />}
          onClick={() => router.push('/registro-exercicios')}
        >
          Voltar
        </Button>
      </Stack>
    );
  }

  // Além de 'in_progress', mostra o botão de finalizar para treinos "antigos"
  // sem session_status definido (criados antes da migração v7 ou importados
  // fora do fluxo de sessão ao vivo) — só esconde quando já está 'done'.
  const canFinish = activity.session_status !== 'done';

  const headerInitialValues: WorkoutHeaderForm = {
    name: activity.name,
    local_date: activity.local_date,
    started_at_time: extractTime(activity.started_at),
    gym_id: activity.gym_id ? String(activity.gym_id) : '',
    training_type_id: activity.training_type_id ? String(activity.training_type_id) : '',
    conducted_by: activity.conducted_by || '',
    workout_plan_id: activity.workout_plan_id ? String(activity.workout_plan_id) : '',
  };

  // Fase 5: tipo de cada série visível e aplicações de técnica (mini-bloco,
  // drop, reps extras) como subitens da série âncora. A edição mantém
  // `set_type`/`technique_code` e remapeia a âncora (ver exercise-set-edit.ts).
  const formSetDisplay = describeSets(form.sets);

  return (
    <Stack gap="xl">
      <Group justify="space-between" align="flex-start">
        <Button
          variant="subtle"
          leftSection={<IconArrowLeft size={16} />}
          onClick={() => router.push('/registro-exercicios')}
        >
          Voltar aos treinos
        </Button>
        <Group>
          {canFinish && (
            <Button
              variant="light"
              color="green"
              leftSection={<IconCheck size={16} />}
              loading={finishMutation.isPending}
              onClick={() => finishMutation.mutate()}
            >
              Finalizar treino
            </Button>
          )}
          <Button variant="default" leftSection={<IconEdit size={16} />} onClick={openHeaderEdit}>
            Editar treino
          </Button>
        </Group>
      </Group>

      <ActivitySummary
        activity={activity}
        onSaveCalories={(kcal) => caloriesMutation.mutateAsync(kcal)}
        savingCalories={caloriesMutation.isPending}
        caloriesError={caloriesMutation.isError ? String(caloriesMutation.error) : null}
      />

      {activity.ai_summary && (
        <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
          <Group gap="xs" mb="xs">
            <IconSparkles size={20} color={BRAND.orange} />
            <Title order={3} c={BRAND.textPrimary}>
              Resumo do treino (IA)
            </Title>
          </Group>
          <Text size="sm" c={BRAND.textMuted}>
            {activity.ai_summary}
          </Text>
          {activity.ai_summary_generated_at && (
            <Text size="xs" c={BRAND.textMuted} mt="xs">
              Gerado em {new Date(activity.ai_summary_generated_at).toLocaleString('pt-BR')}
            </Text>
          )}
        </Paper>
      )}

      {activity.workout_plan_id && planQuery.data && (
        <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
          <Title order={3} mb="sm" c={BRAND.textPrimary}>
            Exercícios da ficha vinculada
          </Title>
          <Stack gap="xs">
            {planQuery.data.exercises.map((planExercise) => {
              const already =
                planExercise.workout_plan_exercise_id != null
                  ? loggedWorkoutPlanExerciseIds.has(planExercise.workout_plan_exercise_id)
                  : planExercise.mapping_id != null && loggedMappingIds.has(planExercise.mapping_id);
              return (
                <Group
                  key={planExercise.workout_plan_exercise_id ?? planExercise.sequence}
                  justify="space-between"
                  wrap="nowrap"
                >
                  <Group gap="xs">
                    <Text size="sm" c={BRAND.textMuted}>
                      {planExercise.sequence}. {planExercise.exercise_name}
                      {planExercise.sets_count ? ` · ${planExercise.sets_count}x${planExercise.reps_target || ''}` : ''}
                    </Text>
                    {already && (
                      <Badge size="sm" color="teal" variant="light">
                        Já lançado
                      </Badge>
                    )}
                  </Group>
                  <Button size="xs" variant="light" onClick={() => prefillFromPlanExercise(planExercise)}>
                    Lançar este
                  </Button>
                </Group>
              );
            })}
          </Stack>
        </Paper>
      )}

      <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
        <Group justify="space-between" mb="md">
          <Title order={3} c={BRAND.textPrimary}>
            {editingExerciseId ? 'Editar exercício' : 'Lançar exercício'}
          </Title>
          {editingExerciseId && (
            <Badge color="orange" variant="light">
              Editando ID {editingExerciseId}
            </Badge>
          )}
        </Group>

        <Stack gap="md">
          <Group grow>
            <Select
              searchable
              label="Grupo muscular"
              placeholder="Selecione o grupo trabalhado"
              data={groupOptions}
              value={form.muscle_group}
              onChange={(value) =>
                setForm((c) => ({ ...c, muscle_group: value || null, equipment_id: '', exercise_name: '' }))
              }
              nothingFoundMessage="Nenhum grupo muscular encontrado"
            />
            <Select
              searchable
              label="Equipamento"
              placeholder={
                !activity.gym_id
                  ? 'Sem academia definida'
                  : !form.muscle_group
                    ? 'Selecione o grupo muscular primeiro'
                    : 'Selecione o equipamento usado'
              }
              data={equipmentOptions}
              value={form.equipment_id || null}
              onChange={(value) => setForm((c) => ({ ...c, equipment_id: value || '', exercise_name: '' }))}
              clearable
              disabled={!activity.gym_id || !form.muscle_group}
              nothingFoundMessage="Nenhum equipamento cadastrado para esse grupo muscular nessa academia"
              leftSection={
                selectedEquipmentOption ? (
                  <EquipmentPhoto photoUrl={selectedEquipmentOption.photo_url} alt={selectedEquipmentOption.label} size={24} />
                ) : undefined
              }
              renderOption={({ option }) => {
                const equipmentOption = option as (typeof equipmentOptions)[number];
                return (
                  <Group gap="xs" wrap="nowrap">
                    <EquipmentPhoto photoUrl={equipmentOption.photo_url} alt={equipmentOption.label} size={28} />
                    <Text size="sm">{equipmentOption.label}</Text>
                  </Group>
                );
              }}
            />
            <Select
              searchable
              label="Exercício canônico"
              placeholder={selectedEquipmentLink ? 'Selecione o exercício' : 'Selecione o equipamento primeiro'}
              data={exerciseOptionsForEquipment}
              value={form.exercise_name || null}
              disabled={!selectedEquipmentLink}
              onChange={(value) => {
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
              nothingFoundMessage="Nenhum exercício encontrado para este equipamento"
            />
          </Group>

          {activity.workout_plan_id && planQuery.data && (
            <Select
              searchable
              clearable
              label="Associar a exercício da ficha"
              placeholder="Nenhum (exercício avulso)"
              data={planExerciseOptions}
              value={form.workout_plan_exercise_id || null}
              onChange={(value) => setForm((c) => ({ ...c, workout_plan_exercise_id: value || '' }))}
              nothingFoundMessage="Nenhum exercício encontrado na ficha"
            />
          )}

          <Group grow align="end">
            <NumberInput
              label="Dor muscular (0-10)"
              description="Opcional — dor muscular residual sentida neste exercício"
              min={0}
              max={10}
              step={1}
              value={form.muscle_pain ?? undefined}
              onChange={(value) =>
                setForm((c) => ({ ...c, muscle_pain: value == null || value === '' ? null : Number(value) }))
              }
              clampBehavior="strict"
            />
            <Switch
              label="Dor articular"
              description="Opcional — reportou dor articular neste exercício"
              checked={form.joint_pain}
              onChange={(event) => {
                const checked = event.currentTarget.checked;
                setForm((c) => ({ ...c, joint_pain: checked }));
              }}
            />
          </Group>

          <Stack gap="xs">
            <Group justify="space-between">
              <Text fw={600} c={BRAND.textPrimary}>
                Séries
              </Text>
              <Button size="xs" variant="default" onClick={addSet}>
                Adicionar série
              </Button>
            </Group>
            {form.sets.map((set, index) => (
              <Group
                key={`${set.set_number}-${index}`}
                align="end"
                pl={formSetDisplay[index]?.isApplication ? 'xl' : undefined}
              >
                <Stack gap={2}>
                  <Badge
                    variant="light"
                    color={
                      formSetDisplay[index]?.isApplication
                        ? 'green'
                        : formSetDisplay[index] != null && isWorkingSetType(formSetDisplay[index].setType)
                          ? 'brand'
                          : 'gray'
                    }
                  >
                    {formSetDisplay[index]?.isApplication ? '↳ ' : ''}
                    {formSetDisplay[index]?.label ?? `Série ${index + 1}`}
                  </Badge>
                  {formSetDisplay[index]?.isApplication && (
                    <Text size="xs" c={BRAND.textMuted}>
                      {getAdvancedTechniqueLabel(set.technique_code) ?? 'Técnica'}
                      {formSetDisplay[index]?.anchorLabel ? ` · âncora: ${formSetDisplay[index]?.anchorLabel}` : ''}
                    </Text>
                  )}
                </Stack>
                {/* M102: série unilateral com lados: reps E/D (as oficiais saem do servidor, pior lado). */}
                {hasSides(set) && setEditFields(set.measure).showReps && (
                  <>
                    <NumberInput
                      label="Reps E"
                      min={0}
                      max={100}
                      value={set.reps_left ?? undefined}
                      onChange={(value) => updateSet(index, 'reps_left', value === '' ? null : Number(value))}
                      w={90}
                    />
                    <NumberInput
                      label="Reps D"
                      min={0}
                      max={100}
                      value={set.reps_right ?? undefined}
                      onChange={(value) => updateSet(index, 'reps_right', value === '' ? null : Number(value))}
                      w={90}
                    />
                  </>
                )}
                {!hasSides(set) && setEditFields(set.measure).showReps && (
                  <NumberInput
                    label="Repetições"
                    min={setEditFields(set.measure).repsMin ?? 1}
                    max={100}
                    value={set.reps ?? undefined}
                    onChange={(value) => updateSet(index, 'reps', value === '' ? null : Number(value))}
                    w={180}
                  />
                )}
                {/* Lote T5: série por tempo (segundos) ou máximo de reps (janela). */}
                {setEditFields(set.measure).showDuration && (
                  <NumberInput
                    label="Tempo (s)"
                    min={setEditFields(set.measure).durationMin}
                    max={setEditFields(set.measure).durationMax}
                    step={1}
                    allowDecimal={false}
                    value={set.duration_seconds ?? undefined}
                    onChange={(value) =>
                      updateSet(index, 'duration_seconds', value === '' || value == null ? null : Number(value))
                    }
                    w={140}
                  />
                )}
                <NumberInput
                  label="Carga (kg)"
                  min={0}
                  max={1000}
                  step={0.5}
                  decimalScale={2}
                  value={set.weight_kg}
                  onChange={(value) => updateSet(index, 'weight_kg', Number(value))}
                  w={180}
                />
                {hasSides(set) ? (
                  <>
                    <NumberInput
                      label="RPE E"
                      min={0}
                      max={10}
                      step={0.5}
                      decimalScale={1}
                      value={set.rpe_left ?? undefined}
                      onChange={(value) => updateSet(index, 'rpe_left', value === '' ? null : Number(value))}
                      w={90}
                    />
                    <NumberInput
                      label="RPE D"
                      min={0}
                      max={10}
                      step={0.5}
                      decimalScale={1}
                      value={set.rpe_right ?? undefined}
                      onChange={(value) => updateSet(index, 'rpe_right', value === '' ? null : Number(value))}
                      w={90}
                    />
                  </>
                ) : (
                  <NumberInput
                    label="RPE"
                    min={0}
                    max={10}
                    step={0.5}
                    decimalScale={1}
                    value={set.rpe ?? undefined}
                    onChange={(value) => updateSet(index, 'rpe', value == null ? null : Number(value))}
                    w={140}
                  />
                )}
                <ActionIcon color="red" variant="subtle" onClick={() => removeSet(index)} disabled={form.sets.length <= 1}>
                  <IconTrash size={16} />
                </ActionIcon>
              </Group>
            ))}
          </Stack>

          <Group>
            <Button
              leftSection={<IconPlus size={16} />}
              onClick={() => saveExerciseMutation.mutate()}
              loading={saveExerciseMutation.isPending}
            >
              {editingExerciseId ? 'Salvar edição' : 'Lançar exercício'}
            </Button>
            <Button variant="default" onClick={resetForm}>
              Limpar
            </Button>
          </Group>

          {saveExerciseMutation.isError && (
            <Alert color="red" icon={<IconAlertTriangle size={16} />}>
              {saveExerciseMutation.error instanceof Error ? saveExerciseMutation.error.message : String(saveExerciseMutation.error)}
            </Alert>
          )}
        </Stack>
      </Paper>

      <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
        <Group justify="space-between" mb="md">
          <Title order={3} c={BRAND.textPrimary}>
            Exercícios lançados
          </Title>
          <Badge color="brand" variant="light">
            {activity.exercises.length} itens
          </Badge>
        </Group>

        <Table.ScrollContainer minWidth={600}>
          <Table striped highlightOnHover withTableBorder>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Exercício</Table.Th>
                <Table.Th>Amazfit</Table.Th>
                <Table.Th>Foto</Table.Th>
                <Table.Th>Equipamento</Table.Th>
                <Table.Th>Séries</Table.Th>
                <Table.Th>Ações</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {activity.exercises.map((row) => (
                <Table.Tr key={row.id}>
                  <Table.Td>
                    <Group gap={6} wrap="nowrap">
                      <Text span>{row.original_name}</Text>
                      {row.workout_plan_exercise_id != null &&
                        row.planned_mapping_id != null &&
                        row.planned_mapping_id !== row.mapping_id && (
                          <Tooltip
                            label={`Substituído — planejado: ${row.planned_exercise_name || 'exercício da ficha'}`}
                            multiline
                            w={260}
                          >
                            <Badge color="orange" variant="light" leftSection={<IconExchange size={12} />}>
                              Trocado
                            </Badge>
                          </Tooltip>
                        )}
                    </Group>
                  </Table.Td>
                  <Table.Td style={{ whiteSpace: 'nowrap' }}>{row.mapped_name || '-'}</Table.Td>
                  <Table.Td>
                    <EquipmentPhoto photoUrl={row.equipment_photo_url} alt={row.equipment_name || 'Equipamento'} size={40} />
                  </Table.Td>
                  <Table.Td>{row.equipment_name || '-'}</Table.Td>
                  <Table.Td>{formatLoggedSets(row.sets)}</Table.Td>
                  <Table.Td>
                    <Group gap="xs">
                      <ActionIcon variant="subtle" color="brand" onClick={() => loadForEdit(row)}>
                        <IconEdit size={16} />
                      </ActionIcon>
                      <ActionIcon
                        variant="subtle"
                        color="red"
                        onClick={() => openDeleteConfirm(row)}
                        loading={deleteExerciseMutation.isPending && deleteTarget?.id === row.id}
                      >
                        <IconTrash size={16} />
                      </ActionIcon>
                    </Group>
                  </Table.Td>
                </Table.Tr>
              ))}
              {activity.exercises.length === 0 && (
                <Table.Tr>
                  <Table.Td colSpan={6}>
                    <Text c={BRAND.textMuted} ta="center" py="md">
                      Nenhum exercício lançado neste treino ainda.
                    </Text>
                  </Table.Td>
                </Table.Tr>
              )}
            </Table.Tbody>
          </Table>
        </Table.ScrollContainer>
      </Paper>

      <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
        <Group justify="space-between" mb="md">
          <Title order={3} c={BRAND.textPrimary}>
            Ativação muscular
          </Title>
          {muscleActivationQuery.isFetching && <Loader size="xs" color="brand" />}
        </Group>

        <MuscleActivationTable
          rows={muscleActivationRows}
          isLoading={muscleActivationQuery.isLoading}
          isError={muscleActivationQuery.isError}
          emptyMessage="Nenhuma ativação muscular calculada para este treino ainda."
          errorMessage="Não foi possível carregar a ativação muscular deste treino."
        />
      </Paper>

      <Modal
        opened={deleteOpen}
        onClose={closeDelete}
        title="Confirmar exclusão"
        size="sm"
        fullScreen={isMobile}
        radius={isMobile ? 0 : undefined}
      >
        <Stack gap="sm">
          <Text>Deseja apagar este exercício do treino?</Text>
          <Text size="sm" c="dimmed">
            {deleteTarget ? deleteTarget.original_name : ''}
          </Text>
          <Group justify="flex-end">
            <Button variant="default" onClick={closeDelete}>
              Cancelar
            </Button>
            <Button
              color="red"
              loading={deleteExerciseMutation.isPending}
              onClick={() => deleteTarget && deleteExerciseMutation.mutate(deleteTarget.id)}
            >
              Apagar
            </Button>
          </Group>
        </Stack>
      </Modal>

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
    </Stack>
  );
}
