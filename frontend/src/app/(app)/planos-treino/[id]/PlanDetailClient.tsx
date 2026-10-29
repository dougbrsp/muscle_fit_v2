'use client';

import dynamic from 'next/dynamic';
import { useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useDisclosure } from '@mantine/hooks';
import {
  ActionIcon,
  Alert,
  Badge,
  Button,
  Center,
  Divider,
  Group,
  Loader,
  Modal,
  Paper,
  Select,
  Stack,
  Text,
  Textarea,
  TextInput,
  Title,
} from '@mantine/core';
import { IconAlertTriangle, IconArrowLeft, IconDeviceWatch, IconHeartbeat, IconSparkles, IconTrash } from '@tabler/icons-react';
import { preloadMarkdown } from '@/components/LazyMarkdown';
import { CardioRecommendationPanel } from '@/components/cardio/CardioRecommendationPanel';
import { apiFetch, ApiError } from '@/lib/api';
import { useDynamicRouteParam } from '@/lib/use-dynamic-route-param';
import type { ApiListResponse, Gym } from '@/lib/gyms-types';
import type { CardioRecommendation, CardioRecommendationsResponse } from '@/lib/analytics-types';
import { dispatchAndPollJob } from '@/lib/async-job-poll';
import {
  planTypeDisplayLabel,
  WORKOUT_PLAN_STATUS_OPTIONS,
  type WorkoutPlan,
  type WorkoutPlanDraft,
  type RefreshPrescriptionResponse,
  type WorkoutPlanExercise,
  type WorkoutPlanInput,
} from '@/lib/workout-plans-types';
import { toWorkoutPlanInput } from '@/lib/workout-plan-payload';
import { indexSwapOptions, type ExerciseSwapOptionsResponse, type SwapEligibilityIndex } from '@/lib/exercise-swap-options';
import { ExercisesTable } from '../ExercisesTable';
import { MuscleActivationPreview } from '../MuscleActivationPreview';
import { GenerationWarnings } from '../GenerationWarnings';
import { PlanMuscleMap } from '../PlanMuscleMap';
import { BRAND } from '@/theme';
import { formatDateOrDash } from '@/lib/format';

// COD-14: modal sob demanda. Fechado ele não desenha nada, e o componente
// continua sempre montado, então o chunk chega logo depois da hidratação,
// antes de o usuário abrir o modal.
const EquipmentSwapModal = dynamic(() => import('../EquipmentSwapModal').then((m) => m.EquipmentSwapModal), { ssr: false });

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

/**
 * Detalhe/edição de um plano de treino já salvo. Reaproveita os mesmos
 * subcomponentes do rascunho recém-gerado (`ExercisesTable`,
 * `MuscleActivationPreview`, `GenerationWarnings`) — a diferença é que aqui
 * o "rascunho" é o próprio plano persistido, e o save chama `PUT
 * /workout-plans/{id}` em vez de `POST /workout-plans`).
 *
 * Recomendação de cardio (Fase 3, paridade onpremise): geração 100% manual —
 * o usuário aciona pelo botão "Gerar recomendação de cardio" abaixo (nunca
 * automática ao salvar, ver `GenerateDraftModal.tsx`). Ao abrir esta tela,
 * busca a recomendação já vinculada a este plano, se existir
 * (`GET /cardio-recommendations?workout_plan_id=&limit=1`).
 */
export function PlanDetailClient() {
  // COD-14: o Markdown da IA vem sob demanda; começa a baixar já na montagem.
  useEffect(preloadMarkdown, []);
  const params = useParams<{ id: string }>();
  // Em `output: 'export'` o `useParams()` devolve o slug do build
  // ("placeholder"), não o id da barra de endereços — ver
  // `useDynamicRouteParam`. Enquanto não resolve, `planId` é `NaN` e a query
  // fica desabilitada por `Number.isFinite`.
  const routeId = useDynamicRouteParam(params.id);
  // `Number(null)` seria 0 (id válido para `Number.isFinite`) — por isso o
  // ternário explícito para NaN enquanto o id não foi resolvido.
  const planId = routeId === null ? Number.NaN : Number(routeId);
  const router = useRouter();
  const qc = useQueryClient();

  const [deleteOpened, { open: openDelete, close: closeDelete }] = useDisclosure(false);

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
  // M8: linha cujo equipamento está sendo trocado (`EquipmentSwapModal`).
  const [swapTarget, setSwapTarget] = useState<WorkoutPlanExercise | null>(null);

  const planQuery = useQuery({
    queryKey: ['workout-plan', planId],
    queryFn: () => apiFetch<WorkoutPlan>(`/workout-plans/${planId}`),
    enabled: Number.isFinite(planId),
  });

  // Exercícios elegíveis para a troca em cada linha salva (regra do servidor,
  // a mesma do 422 `muscle_group_locked`). Relida a cada leitura da ficha:
  // o save e a troca de equipamento invalidam `['workout-plan', planId]`.
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
    mutationFn: (payload: WorkoutPlanInput) => apiFetch<WorkoutPlan>(`/workout-plans/${planId}`, { method: 'PUT', body: JSON.stringify(payload) }),
    onSuccess: (plan) => {
      qc.invalidateQueries({ queryKey: ['workout-plans'] });
      qc.invalidateQueries({ queryKey: ['workout-plan', planId] });
      qc.invalidateQueries({ queryKey: ['plan-muscle-map', planId] });
      setExercises(plan.exercises || []);
    },
  });

  // Prescrição congelada (PR-11): recalcular é sempre um ato EXPLÍCITO. A
  // leitura da ficha nunca recalcula, e o backend só aceita o refresh para
  // ficha `planned` com data de hoje em diante (409 nos demais casos) — por
  // isso o botão só aparece nessa mesma condição.
  const refreshPrescriptionMutation = useMutation({
    mutationFn: () =>
      apiFetch<RefreshPrescriptionResponse>(`/workout-plans/${planId}/refresh-prescription`, { method: 'POST' }),
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: ['workout-plan', planId] });
      setExercises(result.plan.exercises || []);
    },
  });

  // Recomendação de cardio já vinculada a este plano (se existir) —
  // cardio_recommendations é log de auditoria, sem UNIQUE em workout_plan_id,
  // então pode haver mais de uma; `limit=1` pega a mais recente (ORDER BY
  // já feito pelo backend em list_cardio_recommendations).
  const cardioQuery = useQuery({
    queryKey: ['cardio-recommendations-for-plan', planId],
    queryFn: () => apiFetch<CardioRecommendationsResponse>(`/cardio-recommendations?workout_plan_id=${planId}&limit=1`),
    enabled: Number.isFinite(planId),
  });
  const cardioRecommendation = cardioQuery.data?.items?.[0] || null;

  const [cardioDeleteOpened, { open: openCardioDelete, close: closeCardioDelete }] = useDisclosure(false);

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
      closeCardioDelete();
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => apiFetch(`/workout-plans/${planId}`, { method: 'DELETE' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['workout-plans'] });
      router.push('/planos-treino');
    },
  });

  // M8: o servidor gravou o vínculo e recongelou a prescrição da linha; aqui
  // só a linha trocada é substituída no estado local (por
  // `workout_plan_exercise_id`), para não descartar edições ainda não salvas
  // das outras linhas. Um "Salvar alterações" posterior reenvia o mesmo
  // `preferred_equipment_id`/`equipment_name` que o servidor já tem.
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
    // Ficha finalizada: só o cabeçalho é salvo (sem `exercises`, o servidor
    // não toca nos exercícios — e recusaria com 409 se viessem). Só com
    // `editable === false` EXPLÍCITO: um backend anterior a esta regra não
    // manda o campo e trataria a lista ausente como vazia (apagaria tudo).
    const finalized = planQuery.data?.editable === false;
    const payload = toWorkoutPlanInput(
      {
        name,
        plan_type: planType,
        split_day: splitDay || null,
        goal: goal || null,
        status,
        plan_date: planDate || null,
        plan_time: planTime || null,
        gym_id: gymId ? Number(gymId) : null,
        raw_payload: planQuery.data?.raw_payload || null,
        // O UPDATE grava template_id/template_slot_sequence — sem reenviá-los
        // o PUT desvincularia a ficha do modelo de origem.
        template_id: planQuery.data?.template_id ?? null,
        template_slot_sequence: planQuery.data?.template_slot_sequence ?? null,
        template_version_id: planQuery.data?.template_version_id ?? null,
      },
      exercises,
    );
    if (finalized) delete payload.exercises;
    updateMutation.mutate(payload);
  }

  if (planQuery.isLoading) {
    return (
      <Center style={{ minHeight: 300 }}>
        <Loader color="brand" />
      </Center>
    );
  }

  if (planQuery.isError || !planQuery.data) {
    return (
      <Alert color="red" icon={<IconAlertTriangle />} title="Erro ao carregar o plano">
        {errorMessage(planQuery.error, 'Plano de treino não encontrado.')}
      </Alert>
    );
  }

  const rawPayload = (planQuery.data.raw_payload || {}) as Partial<WorkoutPlanDraft>;
  const gymOptions = (gymsQuery.data?.items || []).map((g) => ({ value: String(g.id), label: g.name }));

  // Mesma condição do backend (`services.refresh_plan_prescription`): ficha
  // planejada e com data de hoje em diante. Comparação por string ISO, que é
  // ordenável e não sofre com fuso.
  const savedPlanDate = planQuery.data.plan_date;
  const today = new Date().toLocaleDateString('sv-SE'); // YYYY-MM-DD local
  const canRefreshPrescription =
    planQuery.data.status === 'planned' && (!savedPlanDate || savedPlanDate >= today);
  // Edição dos exercícios: decidida pelo servidor (ficha não finalizada).
  // Campo ausente (backend anterior, janela da promoção) = comportamento
  // antigo: editável.
  const exercisesEditable = planQuery.data.editable !== false;
  // M8: a troca de equipamento vale em toda ficha não finalizada (o servidor
  // aceita data passada: ficha `planned` nunca foi executada) e exige academia
  // SALVA na ficha (o catálogo de opções é o dela; trocar a academia no
  // formulário sem salvar não muda de onde o servidor lê).
  const canSwapEquipment = exercisesEditable && planQuery.data.gym_id != null;
  // Sem resposta (falha da leitura), a linha salva não oferece troca: o
  // servidor é quem decide o que é elegível.
  const swapOptions: SwapEligibilityIndex | 'loading' | undefined = !exercisesEditable
    ? undefined
    : swapOptionsQuery.data
      ? indexSwapOptions(swapOptionsQuery.data)
      : swapOptionsQuery.isError
        ? new Map()
        : 'loading';

  return (
    <Stack gap="xl">
      <Group justify="space-between" align="flex-end" wrap="wrap">
        <Group>
          <ActionIcon variant="subtle" color="brand" onClick={() => router.push('/planos-treino')} aria-label="Voltar">
            <IconArrowLeft size={20} />
          </ActionIcon>
          <div>
            <Title order={1} c={BRAND.textPrimary}>
              {planQuery.data.name}
            </Title>
            <Group gap={6}>
              <Badge color="brand" variant="light">
                {planTypeDisplayLabel(planQuery.data)}
              </Badge>
              {planQuery.data.split_day && (
                <Badge color="brand" variant="filled">
                  Treino {planQuery.data.split_day}
                </Badge>
              )}
            </Group>
          </div>
        </Group>
        <Button variant="subtle" color="red" leftSection={<IconTrash size={16} />} onClick={openDelete}>
          Excluir plano
        </Button>
      </Group>

      <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
        <Stack gap="md">
          <Group grow>
            <TextInput label="Nome da ficha" value={name} onChange={(e) => setName(e.currentTarget.value)} />
            <Select
              label="Status"
              data={WORKOUT_PLAN_STATUS_OPTIONS as unknown as { value: string; label: string }[]}
              value={status}
              onChange={(v) => setStatus(v || 'planned')}
            />
          </Group>
          <Group grow>
            <TextInput label="Data do plano" type="date" value={planDate} onChange={(e) => setPlanDate(e.currentTarget.value)} />
            <TextInput label="Hora pretendida" type="time" value={planTime} onChange={(e) => setPlanTime(e.currentTarget.value)} />
            <Select label="Academia" data={gymOptions} value={gymId} onChange={setGymId} clearable searchable />
          </Group>
          <Textarea label="Objetivo" value={goal} onChange={(e) => setGoal(e.currentTarget.value)} autosize minRows={2} />

          {updateMutation.isError && (
            <Alert color="red" icon={<IconAlertTriangle />} title="Erro ao salvar">
              {errorMessage(updateMutation.error, 'Não foi possível salvar as alterações.')}
            </Alert>
          )}
          {updateMutation.isSuccess && (
            <Alert color="green" title="Ficha atualizada">
              As alterações foram salvas.
            </Alert>
          )}

          <Group justify="flex-end">
            <Button color="brand" loading={updateMutation.isPending} onClick={handleSave} disabled={!name.trim()}>
              Salvar alterações
            </Button>
          </Group>
        </Stack>
      </Paper>

      {(rawPayload.generation_summary || (rawPayload.excluded_due_to_recovery?.length ?? 0) > 0) && (
        <GenerationWarnings draft={rawPayload as WorkoutPlanDraft} />
      )}

      <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
        <Title order={3} c={BRAND.textPrimary} mb="sm">
          Exercícios
        </Title>
        {exercisesEditable ? (
          <Text size="xs" c={BRAND.textMuted} mb="sm">
            Ficha não finalizada: equipamento, séries, reps, carga, RPE, descanso, técnica avançada e demais
            parâmetros podem ser editados. O grupamento muscular de cada exercício fica travado.
          </Text>
        ) : (
          <Alert color="gray" variant="light" mb="sm" title="Ficha finalizada">
            Os exercícios desta ficha ficam somente leitura.
          </Alert>
        )}
        <ExercisesTable
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
        />
      </Paper>

      <EquipmentSwapModal
        opened={swapTarget != null}
        onClose={() => setSwapTarget(null)}
        planId={planId}
        exercise={swapTarget}
        onApplied={handleEquipmentSwapped}
      />

      <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
        <Title order={3} c={BRAND.textPrimary} mb="sm">
          Ativação muscular
        </Title>
        <MuscleActivationPreview exercises={exercises} />
      </Paper>

      <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
        <Group justify="space-between" mb="sm" wrap="wrap">
          <Group gap={6}>
            <IconHeartbeat size={20} color={BRAND.textMuted} />
            <Title order={3} c={BRAND.textPrimary}>
              Recomendação de cardio
            </Title>
          </Group>
          <Group gap={6}>
            <Button
              leftSection={<IconSparkles size={16} />}
              color="brand"
              size="xs"
              loading={generateCardioMutation.isPending}
              onClick={() => generateCardioMutation.mutate()}
            >
              {cardioRecommendation ? 'Gerar novamente' : 'Gerar recomendação de cardio'}
            </Button>
            {cardioRecommendation && (
              <Button variant="subtle" color="red" size="xs" leftSection={<IconTrash size={14} />} onClick={openCardioDelete}>
                Excluir
              </Button>
            )}
          </Group>
        </Group>

        {generateCardioMutation.isError && (
          <Alert color="red" icon={<IconAlertTriangle />} title="Erro ao gerar recomendação" mb="sm">
            {errorMessage(generateCardioMutation.error, 'Não foi possível gerar a recomendação de cardio.')}
          </Alert>
        )}

        {cardioQuery.isLoading && (
          <Group>
            <Loader size="sm" color="brand" />
            <Text c={BRAND.textMuted}>Carregando recomendação vinculada...</Text>
          </Group>
        )}

        {!cardioQuery.isLoading && !cardioRecommendation && !generateCardioMutation.isPending && (
          <Text c={BRAND.textMuted} size="sm">
            Nenhuma recomendação de cardio gerada ainda para esta ficha.
          </Text>
        )}

        {cardioRecommendation && (
          <>
            <Badge color="brand" variant="light" mb="xs">
              {formatDateOrDash(cardioRecommendation.reference_date)}
            </Badge>
            <CardioRecommendationPanel
              recommendationId={cardioRecommendation.id}
              markdown={cardioRecommendation.recommendation_markdown}
              promptVersion={cardioRecommendation.prompt_version}
            />
          </>
        )}
      </Paper>

      <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
        <Group mb="sm">
          <IconDeviceWatch size={20} color={BRAND.textMuted} />
          <Title order={3} c={BRAND.textPrimary}>
            Mapa muscular (app do relógio)
          </Title>
        </Group>
        <PlanMuscleMap planId={planId} />
      </Paper>

      <Modal opened={deleteOpened} onClose={closeDelete} title="Confirmar exclusão" centered>
        <Stack gap="md">
          <Text c={BRAND.textPrimary}>
            Excluir o plano{' '}
            <Text component="span" fw={700}>
              {planQuery.data.name}
            </Text>
            ? Esta ação não pode ser desfeita.
          </Text>

          {deleteMutation.isError && (
            <Alert color="red" icon={<IconAlertTriangle />} title="Erro ao excluir">
              {errorMessage(deleteMutation.error, 'Não foi possível excluir o plano.')}
            </Alert>
          )}

          <Group justify="flex-end">
            <Button variant="default" onClick={closeDelete} disabled={deleteMutation.isPending}>
              Cancelar
            </Button>
            <Button color="red" loading={deleteMutation.isPending} onClick={() => deleteMutation.mutate()}>
              Excluir
            </Button>
          </Group>
        </Stack>
      </Modal>

      {/* cardio_recommendations é log de auditoria imutável (sem PUT/UNDO) —
          exige clique explícito de confirmação, nunca um confirm() nativo. */}
      <Modal opened={cardioDeleteOpened} onClose={closeCardioDelete} title="Confirmar exclusão" centered>
        <Stack gap="md">
          <Text c={BRAND.textPrimary}>
            Excluir a recomendação de cardio de{' '}
            <Text component="span" fw={700}>
              {cardioRecommendation ? formatDateOrDash(cardioRecommendation.reference_date) : ''}
            </Text>
            ? Esta ação não pode ser desfeita — o histórico é um log de auditoria imutável.
          </Text>

          {deleteCardioMutation.isError && (
            <Alert color="red" icon={<IconAlertTriangle />} title="Erro ao excluir">
              {errorMessage(deleteCardioMutation.error, 'Não foi possível excluir a recomendação.')}
            </Alert>
          )}

          <Group justify="flex-end">
            <Button variant="default" onClick={closeCardioDelete} disabled={deleteCardioMutation.isPending}>
              Cancelar
            </Button>
            <Button
              color="red"
              loading={deleteCardioMutation.isPending}
              onClick={() => cardioRecommendation && deleteCardioMutation.mutate(cardioRecommendation.id)}
            >
              Excluir
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
