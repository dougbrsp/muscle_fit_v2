'use client';

import { Alert, Badge, Button, Group, Select, Stack, Text, Textarea, TextInput } from '@mantine/core';
import { IconAlertTriangle, IconSparkles, IconTrash } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import dynamic from 'next/dynamic';
import { useParams, useRouter } from 'next/navigation';
import { useEffect, useState } from 'react';
import { GenerationWarnings } from '@/app/(app)/planos-treino/GenerationWarnings';
import { PlanMuscleMap } from '@/app/(app)/planos-treino/PlanMuscleMap';
import { preloadMarkdown } from '@/components/LazyMarkdown';
import { CardioRecommendationPanel } from '@/components/cardio/CardioRecommendationPanel';
import { ApiError, apiFetch } from '@/lib/api';
import { formatDateOrDash } from '@/lib/format';
import type { CardioRecommendation, CardioRecommendationsResponse } from '@/lib/analytics-types';
import { dispatchAndPollJob } from '@/lib/async-job-poll';
import { indexSwapOptions, type ExerciseSwapOptionsResponse, type SwapEligibilityIndex } from '@/lib/exercise-swap-options';
import type { ApiListResponse, Gym } from '@/lib/gyms-types';
import { useDynamicRouteParam } from '@/lib/use-dynamic-route-param';
import { buildPlanUpdatePayload } from '@/lib/workout-plan-payload';
import {
  planTypeDisplayLabel,
  WORKOUT_PLAN_STATUS_OPTIONS,
  type RefreshPrescriptionResponse,
  type WorkoutPlan,
  type WorkoutPlanDraft,
  type WorkoutPlanExercise,
  type WorkoutPlanInput,
} from '@/lib/workout-plans-types';
import { BottomSheet, ListBlock, ListGroup, ListRow, ScreenHeader, StickyAction } from '../../ui';
import { PortalFieldsStyle } from './PortalFieldsStyle';
import { QueryGate, SheetButton, useBackToClose } from './common';
import { PlanActivationPreviewList } from './PlanActivationPreviewList';
import { PlanExercisesEditor } from './PlanExercisesEditor';

const EquipmentSwapModal = dynamic(
  () => import('@/app/(app)/planos-treino/EquipmentSwapModal').then((m) => m.EquipmentSwapModal),
  { ssr: false },
);

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

/**
 * Detalhe/edição de uma ficha (celular). Mesmas queries, chaves, payloads e
 * guardas do `PlanDetailClient` do desktop (ficha finalizada = só leitura;
 * refresh de prescrição só em `planned` com data de hoje em diante; troca de
 * equipamento exige academia salva). Decisões vêm do servidor (`editable`).
 */
export function PlanoDetalheScreen() {
  useEffect(preloadMarkdown, []);
  const params = useParams<{ id: string }>();
  const routeId = useDynamicRouteParam(params.id);
  const planId = routeId === null ? Number.NaN : Number(routeId);
  const router = useRouter();
  const qc = useQueryClient();

  const [deleteOpen, setDeleteOpen] = useState(false);
  const [cardioDeleteOpen, setCardioDeleteOpen] = useState(false);
  const [name, setName] = useState('');
  const [planType, setPlanType] = useState('full_body');
  const [splitDay, setSplitDay] = useState('');
  const [goal, setGoal] = useState('');
  const [status, setStatus] = useState('planned');
  const [planDate, setPlanDate] = useState('');
  const [planTime, setPlanTime] = useState('');
  const [gymId, setGymId] = useState<string | null>(null);
  const [exercises, setExercises] = useState<WorkoutPlanExercise[]>([]);
  const [hydrated, setHydrated] = useState(false);
  const [swapTarget, setSwapTarget] = useState<WorkoutPlanExercise | null>(null);
  const [editingRow, setEditingRow] = useState(false);
  const [editingHeader, setEditingHeader] = useState(false);
  useBackToClose(editingHeader, () => setEditingHeader(false));

  const planQuery = useQuery({
    queryKey: ['workout-plan', planId],
    queryFn: () => apiFetch<WorkoutPlan>(`/workout-plans/${planId}`),
    enabled: Number.isFinite(planId),
  });
  const swapOptionsQuery = useQuery({
    queryKey: ['workout-plan-swap-options', planId, planQuery.dataUpdatedAt],
    queryFn: () => apiFetch<ExerciseSwapOptionsResponse>(`/workout-plans/${planId}/exercise-swap-options`),
    enabled: Number.isFinite(planId) && planQuery.data?.editable === true,
  });
  const gymsQuery = useQuery({
    queryKey: ['gyms'],
    queryFn: () => apiFetch<ApiListResponse<Gym>>('/gyms?active_only=true'),
  });

  useEffect(() => {
    const plan = planQuery.data;
    if (!plan || hydrated) return;
    setName(plan.name);
    setPlanType(plan.plan_type);
    setSplitDay(plan.split_day || '');
    setGoal(plan.goal || '');
    setStatus(plan.status);
    setPlanDate(plan.plan_date || '');
    setPlanTime(plan.plan_time || '');
    setGymId(plan.gym_id ? String(plan.gym_id) : null);
    setExercises(plan.exercises || []);
    setHydrated(true);
  }, [planQuery.data, hydrated]);

  const updateMutation = useMutation({
    mutationFn: (payload: WorkoutPlanInput) =>
      apiFetch<WorkoutPlan>(`/workout-plans/${planId}`, { method: 'PUT', body: JSON.stringify(payload) }),
    onSuccess: (plan) => {
      qc.invalidateQueries({ queryKey: ['workout-plans'] });
      qc.invalidateQueries({ queryKey: ['workout-plan', planId] });
      qc.invalidateQueries({ queryKey: ['plan-muscle-map', planId] });
      setExercises(plan.exercises || []);
    },
  });

  const refreshPrescriptionMutation = useMutation({
    mutationFn: () => apiFetch<RefreshPrescriptionResponse>(`/workout-plans/${planId}/refresh-prescription`, { method: 'POST' }),
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: ['workout-plan', planId] });
      setExercises(result.plan.exercises || []);
    },
  });

  const cardioQuery = useQuery({
    queryKey: ['cardio-recommendations-for-plan', planId],
    queryFn: () => apiFetch<CardioRecommendationsResponse>(`/cardio-recommendations?workout_plan_id=${planId}&limit=1`),
    enabled: Number.isFinite(planId),
  });
  const cardioRecommendation = cardioQuery.data?.items?.[0] || null;

  const generateCardioMutation = useMutation({
    mutationFn: () => {
      const proposedWorkout = `${planQuery.data?.name}: ${(planQuery.data?.exercises || []).map((ex) => ex.exercise_name).join(', ')}`;
      return dispatchAndPollJob<CardioRecommendation>('/cardio-recommendations/generate', {
        body: JSON.stringify({
          days: 14,
          proposed_workout: proposedWorkout,
          reference_date: planQuery.data?.plan_date || undefined,
          workout_plan_id: planId,
        }),
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['cardio-recommendations-for-plan', planId] });
    },
  });

  const deleteCardioMutation = useMutation({
    mutationFn: (id: number) => apiFetch(`/cardio-recommendations/${id}`, { method: 'DELETE' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['cardio-recommendations-for-plan', planId] });
      setCardioDeleteOpen(false);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => apiFetch(`/workout-plans/${planId}`, { method: 'DELETE' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['workout-plans'] });
      router.push('/planos-treino');
    },
  });

  function handleEquipmentSwapped(updated: WorkoutPlanExercise) {
    setExercises((current) =>
      current.map((ex) =>
        ex.workout_plan_exercise_id === updated.workout_plan_exercise_id ? { ...ex, ...updated } : ex,
      ),
    );
    qc.invalidateQueries({ queryKey: ['workout-plan', planId] });
    qc.invalidateQueries({ queryKey: ['plan-muscle-map', planId] });
  }

  function handleSave() {
    const payload = buildPlanUpdatePayload(
      planQuery.data as WorkoutPlan,
      { name, plan_type: planType, split_day: splitDay, goal, status, plan_date: planDate, plan_time: planTime, gym_id: gymId },
      exercises,
    );
    updateMutation.mutate(payload);
  }

  const plan = planQuery.data;
  if (!plan) {
    return (
      <>
        <ScreenHeader title="Plano" />
        <QueryGate
          isLoading={planQuery.isLoading}
          isError
          error={planQuery.error ?? new Error('Plano de treino não encontrado.')}
          onRetry={() => void planQuery.refetch()}
          errorTitle="Erro ao carregar o plano"
        >
          {null}
        </QueryGate>
      </>
    );
  }

  const rawPayload = (plan.raw_payload || {}) as Partial<WorkoutPlanDraft>;
  const gymOptions = (gymsQuery.data?.items || []).map((g) => ({ value: String(g.id), label: g.name }));
  const savedPlanDate = plan.plan_date;
  const today = new Date().toLocaleDateString('sv-SE');
  const canRefreshPrescription = plan.status === 'planned' && (!savedPlanDate || savedPlanDate >= today);
  const exercisesEditable = plan.editable !== false;
  const canSwapEquipment = exercisesEditable && plan.gym_id != null;
  const swapOptions: SwapEligibilityIndex | 'loading' | undefined = !exercisesEditable
    ? undefined
    : swapOptionsQuery.data
      ? indexSwapOptions(swapOptionsQuery.data)
      : swapOptionsQuery.isError
        ? new Map()
        : 'loading';

  return (
    <>
      {editingRow ? null : (
        <>
          <ScreenHeader title={plan.name} subtitle={[planTypeDisplayLabel(plan), plan.split_day ? `Treino ${plan.split_day}` : null].filter(Boolean).join(' · ')} />
          {editingHeader ? (
            <>
          <Stack gap="md" mb="md">
            <TextInput size="md" label="Nome da ficha" value={name} onChange={(e) => setName(e.currentTarget.value)} />
            <Select
              size="md"
              label="Status"
              data={WORKOUT_PLAN_STATUS_OPTIONS as unknown as { value: string; label: string }[]}
              value={status}
              onChange={(v) => setStatus(v || 'planned')}
              allowDeselect={false}
            />
            <Group grow align="flex-start">
              <TextInput size="md" label="Data do plano" type="date" value={planDate} onChange={(e) => setPlanDate(e.currentTarget.value)} />
              <TextInput size="md" label="Hora pretendida" type="time" value={planTime} onChange={(e) => setPlanTime(e.currentTarget.value)} />
            </Group>
            <Select size="md" label="Academia" data={gymOptions} value={gymId} onChange={setGymId} clearable searchable />
            <Textarea size="md" label="Objetivo" value={goal} onChange={(e) => setGoal(e.currentTarget.value)} autosize minRows={2} />
            {updateMutation.isError ? (
              <Alert color="red" icon={<IconAlertTriangle />} title="Erro ao salvar" role="alert">
                {errorMessage(updateMutation.error, 'Não foi possível salvar as alterações.')}
              </Alert>
            ) : null}
            {updateMutation.isSuccess ? (
              <Alert color="green" title="Ficha atualizada">
                As alterações foram salvas.
              </Alert>
            ) : null}
          </Stack>
              <Button variant="light" color="brand" fullWidth mih={48} mb="md" onClick={() => setEditingHeader(false)}>
                Concluir edição dos dados
              </Button>
            </>
          ) : (
            <ListGroup title="Dados da ficha">
              <ListRow title="Status" value={WORKOUT_PLAN_STATUS_OPTIONS.find((o) => o.value === status)?.label ?? status} onClick={() => setEditingHeader(true)} />
              <ListRow title="Data e hora" value={[formatDateOrDash(planDate), planTime].filter(Boolean).join(' ')} onClick={() => setEditingHeader(true)} />
              <ListRow title="Academia" value={gymOptions.find((g) => g.value === gymId)?.label ?? '-'} onClick={() => setEditingHeader(true)} />
              <ListRow title="Objetivo" subtitle={goal || undefined} value={goal ? undefined : '-'} onClick={() => setEditingHeader(true)} />
              {updateMutation.isSuccess ? <ListRow title="Alterações salvas" /> : null}
            </ListGroup>
          )}

          {rawPayload.generation_summary || (rawPayload.excluded_due_to_recovery?.length ?? 0) > 0 ? (
            <Stack mb="md">
              <GenerationWarnings draft={rawPayload as WorkoutPlanDraft} />
            </Stack>
          ) : null}

          {exercisesEditable ? null : (
            <Alert color="gray" variant="light" mb="sm" title="Ficha finalizada">
              Os exercícios ficam somente leitura.
            </Alert>
          )}
        </>
      )}

      <PlanExercisesEditor
        exercises={exercises}
        onChange={setExercises}
        readOnly={!exercisesEditable}
        swapOptions={swapOptions}
        onRefreshPrescription={canRefreshPrescription ? () => refreshPrescriptionMutation.mutate() : undefined}
        refreshingPrescription={refreshPrescriptionMutation.isPending}
        refreshPrescriptionError={
          refreshPrescriptionMutation.isError
            ? errorMessage(refreshPrescriptionMutation.error, 'Não foi possível atualizar a prescrição.')
            : null
        }
        onSwapEquipment={canSwapEquipment ? setSwapTarget : undefined}
        onEditingChange={setEditingRow}
      />

      {editingRow ? null : (
        <>
          <PlanActivationPreviewList exercises={exercises} />

          <ListGroup title="Recomendação de cardio">
            {cardioQuery.isLoading ? <ListBlock>Carregando recomendação vinculada...</ListBlock> : null}
            {!cardioQuery.isLoading && !cardioRecommendation && !generateCardioMutation.isPending ? (
              <ListBlock>
                <Text size="sm" c="dimmed">
                  Nenhuma recomendação de cardio gerada ainda para esta ficha.
                </Text>
              </ListBlock>
            ) : null}
            {cardioRecommendation ? (
              <ListBlock>
                <Badge color="brand" variant="light" mb="xs">
                  {formatDateOrDash(cardioRecommendation.reference_date)}
                </Badge>
                <CardioRecommendationPanel
                  recommendationId={cardioRecommendation.id}
                  markdown={cardioRecommendation.recommendation_markdown}
                  promptVersion={cardioRecommendation.prompt_version}
                />
              </ListBlock>
            ) : null}
            <ListRow
              icon={IconSparkles}
              title={
                generateCardioMutation.isPending
                  ? 'Gerando...'
                  : cardioRecommendation
                    ? 'Gerar novamente'
                    : 'Gerar recomendação de cardio'
              }
              onClick={generateCardioMutation.isPending ? undefined : () => generateCardioMutation.mutate()}
            />
            {cardioRecommendation ? (
              <ListRow icon={IconTrash} destructive title="Excluir recomendação" onClick={() => setCardioDeleteOpen(true)} />
            ) : null}
          </ListGroup>
          {generateCardioMutation.isError ? (
            <Alert color="red" icon={<IconAlertTriangle />} title="Erro ao gerar recomendação" mb="md" role="alert">
              {errorMessage(generateCardioMutation.error, 'Não foi possível gerar a recomendação de cardio.')}
            </Alert>
          ) : null}

          <ListGroup title="Mapa muscular (app do relógio)">
            <ListBlock>
              <PlanMuscleMap planId={planId} />
            </ListBlock>
          </ListGroup>

          <ListGroup>
            <ListRow icon={IconTrash} destructive title="Excluir plano" onClick={() => setDeleteOpen(true)} />
          </ListGroup>

          <StickyAction>
            <Button fullWidth size="lg" color="brand" mih={48} loading={updateMutation.isPending} disabled={!name.trim()} onClick={handleSave}>
              Salvar alterações
            </Button>
          </StickyAction>
        </>
      )}

      <PortalFieldsStyle />
      <EquipmentSwapModal
        opened={swapTarget != null}
        onClose={() => setSwapTarget(null)}
        planId={planId}
        exercise={swapTarget}
        onApplied={handleEquipmentSwapped}
      />

      <BottomSheet
        opened={deleteOpen}
        onClose={() => setDeleteOpen(false)}
        title="Confirmar exclusão"
        footer={
          <>
            <SheetButton variant="default" disabled={deleteMutation.isPending} onClick={() => setDeleteOpen(false)}>
              Cancelar
            </SheetButton>
            <SheetButton color="red" loading={deleteMutation.isPending} onClick={() => deleteMutation.mutate()}>
              Excluir
            </SheetButton>
          </>
        }
      >
        <Text>
          Excluir o plano <b>{plan.name}</b>? Esta ação não pode ser desfeita.
        </Text>
        {deleteMutation.isError ? (
          <Text size="sm" c="red" role="alert">
            {errorMessage(deleteMutation.error, 'Não foi possível excluir o plano.')}
          </Text>
        ) : null}
      </BottomSheet>

      <BottomSheet
        opened={cardioDeleteOpen}
        onClose={() => setCardioDeleteOpen(false)}
        title="Confirmar exclusão"
        footer={
          <>
            <SheetButton variant="default" disabled={deleteCardioMutation.isPending} onClick={() => setCardioDeleteOpen(false)}>
              Cancelar
            </SheetButton>
            <SheetButton
              color="red"
              loading={deleteCardioMutation.isPending}
              onClick={() => cardioRecommendation && deleteCardioMutation.mutate(cardioRecommendation.id)}
            >
              Excluir
            </SheetButton>
          </>
        }
      >
        <Text>
          Excluir a recomendação de cardio de{' '}
          <b>{cardioRecommendation ? formatDateOrDash(cardioRecommendation.reference_date) : ''}</b>? Esta ação não pode ser
          desfeita: o histórico é um log de auditoria imutável.
        </Text>
        {deleteCardioMutation.isError ? (
          <Text size="sm" c="red" role="alert">
            {errorMessage(deleteCardioMutation.error, 'Não foi possível excluir a recomendação.')}
          </Text>
        ) : null}
      </BottomSheet>
    </>
  );
}
