'use client';

import {
  Alert,
  Badge,
  Button,
  Card,
  Group,
  Loader,
  Rating,
  SegmentedControl,
  SimpleGrid,
  Stack,
  Text,
  Title,
  UnstyledButton,
} from '@mantine/core';
import { IconAlertTriangle, IconArrowLeft, IconCheck, IconPlayerStop } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { apiFetch, ApiError } from '@/lib/api';
import { EquipmentPhoto } from '@/components/EquipmentPhoto';
import {
  EQUIPMENT_EXERCISE_PREFERENCES_PATH,
  EQUIPMENT_EXERCISE_PREFERENCES_QUERY_KEY,
  MAX_PREFERENCE_STARS,
  pairKey,
  sortExercisesByPairStars,
  starsByPairKey,
  starsLabel,
  type EquipmentExercisePreferencesResponse,
} from '@/lib/equipment-recommend';
import {
  clampRpeSuggestion,
  parseNumericTarget,
  type ApiList,
  type ExerciseSummaryResult,
  type GymEquipmentLink,
  type LastSessionSetsResponse,
  type MuscleGroupsResponse,
  type SetEvaluationResult,
  type SuggestedProgressionResponse,
} from '@/lib/workout-mode-types';
import { fetchWorkoutAiResult } from '@/lib/workout-mode-ai';
import { resolveNextSetPreview } from '@/lib/workout-mode-next-set';
import { DEFAULT_MEASURE, MEASURE_OPTIONS } from '@/lib/set-measures';
import { measureLoggerDefaults, setMeasurePayload } from '@/lib/workout-mode-measures';
import { selectPreviousSessionRecord } from '@/lib/workout-mode-previous-session';
import { selectPreviousSessionWorkingSets } from '@/lib/workout-mode-previous-session-sets';
import { finishWorkoutSession } from '@/lib/workout-session-finish';
import { BRAND } from '@/theme';
import {
  DEFAULT_REST_SECONDS,
  SET_LOGGER_FALLBACK_LOAD_KG,
} from '@/lib/prescription-defaults';
import { RestTimer } from './RestTimer';
import { useWeightUnit } from '@/lib/use-weight-unit';
import { ExerciseSummaryCard, SetEvaluationCard, SetLoggerCard, type SetLoggerValues } from './SetLoggerCard';

type Phase = 'pick-group' | 'pick-equipment' | 'pick-exercise' | 'logging' | 'exercise-done';

type EquipmentOption = { equipment_id: number; equipment_name: string; photo_url: string | null };

type ExerciseOption = {
  link_id: number;
  mapping_id: number;
  exercise_name: string;
  primary_group: string | null;
  secondary_group: string | null;
};

// `reps`/`weight_kg` nulos só fora de repetições (Lote T5); `measure`/
// `duration_seconds` só aparecem nessas séries (`setMeasurePayload`).
type LoggedSet = {
  set_number: number;
  reps: number | null;
  weight_kg: number | null;
  rpe: number | null;
  measure?: string;
  duration_seconds?: number | null;
};

type AiState<T> = { loading: boolean; error: boolean; result: T | null };

const IDLE_AI = { loading: false, error: false, result: null };

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

type Props = {
  sessionId: number;
  gymId?: number | null;
  perSetAiEnabled: boolean;
  onFinish: (sessionId: number) => void;
  // Quando presente, esta tela está sendo usada DENTRO do fluxo guiado por
  // ficha (`PlanExecutionScreen`, opção "Adicionar exercício avulso") — expõe
  // o retorno à ficha. Ausente no uso normal em `/modo-treino`.
  title?: string;
  onReturnToPlan?: () => void;
};

/**
 * Execução de treino avulso (sem ficha vinculada): escolha
 * Grupo muscular → Equipamento → Exercício, um exercício de cada vez, com a
 * mesma mecânica de registro de série/descanso das telas guiadas.
 *
 * Passos 1/2/3 reaproveitam exatamente os mesmos endpoints de
 * `WorkoutDetailClient.tsx` (`GET /exercise-mapping-catalog/groups` e
 * `GET /gym-equipment?gym_id&primary_group&active_only=true`), e o
 * lançamento usa os endpoints incrementais de sessão ao vivo
 * (`POST /workout-sessions/{id}/exercises` e `.../sets`, uma série por
 * chamada). `onFinish` só é chamado após `POST /workout-sessions/{id}/finish`
 * responder com sucesso — o orquestrador (`WorkoutModeClient`) cuida do
 * resumo de IA do treino e da navegação.
 */
export function FreeformScreen({
  sessionId,
  gymId,
  perSetAiEnabled,
  onFinish,
  title = 'Treino avulso',
  onReturnToPlan,
}: Props) {
  const queryClient = useQueryClient();
  const [phase, setPhase] = useState<Phase>('pick-group');
  const [selectedGroup, setSelectedGroup] = useState<string | null>(null);
  const [selectedEquipment, setSelectedEquipment] = useState<EquipmentOption | null>(null);
  const [selectedExercise, setSelectedExercise] = useState<ExerciseOption | null>(null);
  const [sessionExerciseId, setSessionExerciseId] = useState<number | null>(null);
  // Lote T5: medida das séries do exercício livre (opções do espelho do dono).
  // Escolhida antes da 1ª série; volta a "Repetições" a cada exercício novo.
  const [freeMeasure, setFreeMeasure] = useState<string>(DEFAULT_MEASURE);
  const [loggedSets, setLoggedSets] = useState<LoggedSet[]>([]);
  const [resting, setResting] = useState(false);
  const [suggested, setSuggested] = useState<SuggestedProgressionResponse | null>(null);
  const [evaluation, setEvaluation] = useState<AiState<SetEvaluationResult>>(IDLE_AI);
  const [exerciseSummary, setExerciseSummary] = useState<AiState<ExerciseSummaryResult>>(IDLE_AI);

  // Passo 1 — grupos musculares do catálogo canônico.
  const groupsQuery = useQuery({
    queryKey: ['mapping-catalog-groups'],
    queryFn: () => apiFetch<MuscleGroupsResponse>('/exercise-mapping-catalog/groups'),
  });

  // Passo 2/3 — equipamentos desta academia que atendem o grupo escolhido; a
  // mesma lista já contém, por linha, o exercício canônico que cada
  // equipamento atende (passo 3).
  const gymEquipmentQuery = useQuery({
    queryKey: ['workout-mode-freeform-gym-equipment', gymId, selectedGroup],
    queryFn: () =>
      apiFetch<ApiList<GymEquipmentLink>>(
        `/gym-equipment?gym_id=${gymId}&primary_group=${encodeURIComponent(selectedGroup || '')}&active_only=true`,
      ),
    enabled: Boolean(gymId && selectedGroup),
  });

  // Estrelas do usuário por PAR exercício+equipamento (mesma query do Domínio
  // de exercícios, onde a nota é dada). Como a nota é do par, ela aparece no
  // passo 3 (exercício, já dentro do equipamento), não no passo 2. Treino
  // livre não tem técnica prescrita, então não há `required_technique` aqui.
  // Falha nesta leitura não bloqueia o treino: ordena só por nome.
  const preferencesQuery = useQuery({
    queryKey: EQUIPMENT_EXERCISE_PREFERENCES_QUERY_KEY,
    queryFn: () => apiFetch<EquipmentExercisePreferencesResponse>(EQUIPMENT_EXERCISE_PREFERENCES_PATH),
    staleTime: 60_000,
    retry: false,
  });
  const pairStars = useMemo(() => starsByPairKey(preferencesQuery.data?.items), [preferencesQuery.data]);

  const equipmentOptions = useMemo<EquipmentOption[]>(() => {
    const byId = new Map<number, EquipmentOption>();
    for (const link of gymEquipmentQuery.data?.items || []) {
      if (!byId.has(link.equipment_id)) {
        byId.set(link.equipment_id, {
          equipment_id: link.equipment_id,
          equipment_name: link.equipment_name,
          photo_url: link.photo_url ?? null,
        });
      }
    }
    return Array.from(byId.values()).sort((a, b) => a.equipment_name.localeCompare(b.equipment_name));
  }, [gymEquipmentQuery.data]);

  const exerciseOptions = useMemo<ExerciseOption[]>(() => {
    if (!selectedEquipment) return [];
    const byName = new Map<string, ExerciseOption>();
    for (const link of gymEquipmentQuery.data?.items || []) {
      if (link.equipment_id !== selectedEquipment.equipment_id) continue;
      const name = link.mapping_exercise_name || link.equipment_name;
      if (byName.has(name)) continue;
      byName.set(name, {
        link_id: link.id,
        mapping_id: link.mapping_id,
        exercise_name: name,
        primary_group: link.primary_group ?? null,
        secondary_group: link.secondary_group ?? null,
      });
    }
    // Mais estrelas NESTE equipamento primeiro; sem avaliação = 3; depois nome.
    return sortExercisesByPairStars(Array.from(byName.values()), selectedEquipment.equipment_id, pairStars);
  }, [gymEquipmentQuery.data, selectedEquipment, pairStars]);

  const equipmentPhotoUrl = selectedEquipment?.photo_url ?? null;

  // Referência histórica por série (última sessão do MESMO exercício
  // canônico) — nunca vira default do input, só é exibida no card.
  const previousSessionSetsQuery = useQuery({
    queryKey: ['last-session-sets', selectedExercise?.mapping_id, sessionId, selectedExercise?.link_id],
    queryFn: () =>
      apiFetch<LastSessionSetsResponse>(
        `/exercises/last-session-sets?mapping_id=${selectedExercise?.mapping_id}&exclude_activity_id=${sessionId}` +
          (selectedExercise?.link_id ? `&equipment_id=${selectedExercise.link_id}` : ''),
      ),
    enabled: Boolean(selectedExercise?.mapping_id),
  });
  // M21: sessão anterior inteira (só séries de trabalho) — ver
  // `selectPreviousSessionWorkingSets`.
  const previousSessionSets = useMemo(
    () => selectPreviousSessionWorkingSets(previousSessionSetsQuery.data),
    [previousSessionSetsQuery.data],
  );
  // Recorde + e1RM do histórico nesse equipamento (mesma resposta).
  const previousSessionRecord = useMemo(
    () => selectPreviousSessionRecord(previousSessionSetsQuery.data),
    [previousSessionSetsQuery.data],
  );
  // M79: fadiga atual do grupo (mesma resposta, faixa e rótulo prontos do
  // backend), num chip único no hero. Carregando: `undefined` (sem chip);
  // erro na consulta: "Fadiga indisponível".
  const currentGroupFatigue = previousSessionSetsQuery.isSuccess
    ? (previousSessionSetsQuery.data?.current_group_fatigue ?? null)
    : previousSessionSetsQuery.isError
      ? null
      : undefined;

  // M3: ver comentário equivalente em `PlanExecutionScreen`. O backend só
  // recebe kg.
  const { unit: weightUnit, setUnit: setWeightUnit } = useWeightUnit();

  const pickExerciseMutation = useMutation({
    mutationFn: async (option: ExerciseOption) => {
      // Sugestão de progressão é best-effort: sem histórico (404) o
      // lançamento continua normalmente com os defaults do card.
      // `equipment_id` escolhe a trilha do par escolhido: carga, teto de RPE e
      // passo do equipamento vêm do motor v2 para ESTE equipamento (M76).
      const suggestion = await apiFetch<SuggestedProgressionResponse>(
        `/exercises/suggested-progression?mapping_id=${option.mapping_id}&equipment_id=${option.link_id}`,
      ).catch(() => null);

      const created = await apiFetch<{ exercise_id: number }>(`/workout-sessions/${sessionId}/exercises`, {
        method: 'POST',
        body: JSON.stringify({
          original_name: option.exercise_name,
          mapping_id: option.mapping_id,
          equipment_id: option.link_id,
        }),
      });

      return { created, suggestion };
    },
    onSuccess: ({ created, suggestion }, option) => {
      setSelectedExercise(option);
      setSessionExerciseId(created.exercise_id);
      setSuggested(suggestion);
      setLoggedSets([]);
      setFreeMeasure(DEFAULT_MEASURE);
      setResting(false);
      setEvaluation(IDLE_AI);
      setExerciseSummary(IDLE_AI);
      setPhase('logging');
    },
  });

  const saveSetMutation = useMutation({
    mutationFn: async (values: SetLoggerValues) => {
      if (!sessionExerciseId) throw new ApiError('Exercício não iniciado nesta sessão.', 0);
      const setNumber = loggedSets.length + 1;
      const previousSets = loggedSets;
      await apiFetch(`/workout-sessions/${sessionId}/exercises/${sessionExerciseId}/sets`, {
        method: 'POST',
        body: JSON.stringify({
          set_number: setNumber,
          ...(values.set_type ? { set_type: values.set_type } : {}),
          reps: values.reps,
          weight_kg: values.weight_kg,
          rpe: values.rpe,
          // Lote T5: `{}` em série de repetições (payload igual ao de antes).
          ...setMeasurePayload(values),
          rest_seconds: DEFAULT_REST_SECONDS,
          muscle_pain: values.muscle_pain,
          joint_pain: values.joint_pain,
          notes: values.notes,
        }),
      });
      return { setNumber, values, previousSets };
    },
    onSuccess: ({ setNumber, values, previousSets }) => {
      setLoggedSets((current) => [
        ...current,
        {
          set_number: setNumber,
          reps: values.reps,
          weight_kg: values.weight_kg,
          rpe: values.rpe,
          ...setMeasurePayload(values),
        },
      ]);
      queryClient.invalidateQueries({ queryKey: ['activity-detail-workout-mode', sessionId] });
      setResting(true);
      if (perSetAiEnabled) {
        runEvaluation(setNumber, values, previousSets);
      } else {
        setEvaluation(IDLE_AI);
      }
    },
  });

  // Avaliação de IA por série — só quando o usuário ativou o toggle "IA por
  // série". Nunca bloqueia o treino: roda em paralelo ao `RestTimer` e um
  // erro vira "avaliação indisponível" no card.
  function runEvaluation(setNumber: number, values: SetLoggerValues, previousSets: LoggedSet[]) {
    if (!selectedExercise) return;
    setEvaluation({ loading: true, error: false, result: null });
    fetchWorkoutAiResult<SetEvaluationResult>('/exercises/evaluate-set', {
      method: 'POST',
      body: JSON.stringify({
        exercise_name: selectedExercise.exercise_name,
        // R1 (regras de carga): sessão + `exercises.id` para o servidor ler a
        // faixa de fadiga prévia do exercício (o mesmo leitor do salvamento).
        session_id: sessionId,
        exercise_id: sessionExerciseId,
        reps_target: suggested?.reps_target || null,
        load_target: suggested?.load_target || null,
        rpe_target: suggested?.rpe_target || null,
        reps: values.reps,
        weight_kg: values.weight_kg,
        rpe: values.rpe,
        ...setMeasurePayload(values),
        set_number: setNumber,
        sets_count: null,
        // Par + teto do servidor (M76): a IA devolve a sugestão já cortada.
        mapping_id: selectedExercise.mapping_id,
        equipment_id: selectedExercise.link_id,
        effective_rpe_ceiling: suggested?.effective_rpe_ceiling ?? null,
        muscle_pain: values.muscle_pain,
        joint_pain: values.joint_pain,
        notes: values.notes,
        previous_sets_this_session: previousSets,
      }),
    })
      .then((result) => setEvaluation({ loading: false, error: false, result }))
      .catch(() => setEvaluation({ loading: false, error: true, result: null }));
  }

  // Resumo de fim de EXERCÍCIO — incondicional (independe de
  // `perSetAiEnabled`), disparado ao encerrar o exercício, que é o ponto
  // natural de conclusão aqui (não existe "próximo item da ficha").
  function runExerciseSummary(exerciseId: number) {
    setExerciseSummary({ loading: true, error: false, result: null });
    fetchWorkoutAiResult<ExerciseSummaryResult>(`/exercises/${exerciseId}/summarize`, { method: 'POST' })
      .then((result) => setExerciseSummary({ loading: false, error: false, result }))
      .catch(() => setExerciseSummary({ loading: false, error: true, result: null }));
  }

  const finishMutation = useMutation({
    mutationFn: () => finishWorkoutSession(sessionId),
    onSuccess: () => onFinish(sessionId),
  });

  function finishExercise() {
    if (sessionExerciseId) runExerciseSummary(sessionExerciseId);
    setResting(false);
    setPhase('exercise-done');
  }

  function startNewExercise() {
    setSelectedGroup(null);
    setSelectedEquipment(null);
    setSelectedExercise(null);
    setSessionExerciseId(null);
    setSuggested(null);
    setLoggedSets([]);
    setResting(false);
    setEvaluation(IDLE_AI);
    setExerciseSummary(IDLE_AI);
    setPhase('pick-group');
  }

  const backToPlanButton = onReturnToPlan ? (
    <Button variant="subtle" size="xs" leftSection={<IconArrowLeft size={16} />} onClick={onReturnToPlan}>
      Voltar para a ficha
    </Button>
  ) : null;

  const finishError = finishMutation.isError ? (
    <Alert color="red" variant="light" icon={<IconAlertTriangle size={16} />}>
      {errorMessage(finishMutation.error, 'Não foi possível finalizar o treino.')}
    </Alert>
  ) : null;

  if (phase === 'pick-group') {
    const groups = groupsQuery.data?.items || [];
    return (
      <Stack gap="md" py="md">
        {backToPlanButton}
        <Title order={2} c={BRAND.textPrimary}>
          {title}
        </Title>
        <Text size="sm" c={BRAND.textMuted}>
          Passo 1 de 3 — escolha o grupo muscular que vai treinar agora.
        </Text>
        {!gymId && (
          <Alert color="yellow" variant="light" icon={<IconAlertTriangle size={16} />}>
            <Text size="sm">
              Nenhuma academia selecionada nesta sessão — sem academia não há catálogo de equipamentos para sugerir.
            </Text>
          </Alert>
        )}
        {groupsQuery.isLoading && <Loader size="sm" color="brand" />}
        {groupsQuery.isError && (
          <Alert color="red" variant="light" icon={<IconAlertTriangle size={16} />}>
            <Text size="sm">Não foi possível carregar os grupos musculares.</Text>
          </Alert>
        )}
        <SimpleGrid cols={2} spacing="sm">
          {groups.map((group) => (
            <UnstyledButton
              key={group.primary_group}
              onClick={() => {
                setSelectedGroup(group.primary_group);
                setPhase('pick-equipment');
              }}
            >
              <Card withBorder radius="xl" p="md" ta="center" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
                <Text fw={700} c={BRAND.textPrimary}>
                  {group.primary_group}
                </Text>
                <Text size="xs" c={BRAND.textMuted}>
                  {group.count} exercício(s)
                </Text>
              </Card>
            </UnstyledButton>
          ))}
        </SimpleGrid>
        <Button
          size="lg"
          radius="xl"
          h={56}
          variant="light"
          color="green"
          leftSection={<IconPlayerStop size={18} />}
          loading={finishMutation.isPending}
          onClick={() => finishMutation.mutate()}
        >
          Encerrar treino
        </Button>
        {finishError}
      </Stack>
    );
  }

  if (phase === 'pick-equipment') {
    return (
      <Stack gap="md" py="md">
        <Button variant="subtle" leftSection={<IconArrowLeft size={16} />} onClick={() => setPhase('pick-group')}>
          Voltar
        </Button>
        <Title order={3} c={BRAND.textPrimary}>
          {selectedGroup}
        </Title>
        <Text size="sm" c={BRAND.textMuted}>
          Passo 2 de 3 — escolha o equipamento.
        </Text>
        {gymEquipmentQuery.isLoading && <Loader size="sm" color="brand" />}
        <SimpleGrid cols={2} spacing="sm">
          {equipmentOptions.map((option) => (
            <UnstyledButton
              key={option.equipment_id}
              onClick={() => {
                setSelectedEquipment(option);
                setPhase('pick-exercise');
              }}
            >
              <Card withBorder radius="xl" p="sm" ta="center" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
                <Group justify="center">
                  <EquipmentPhoto photoUrl={option.photo_url} alt={option.equipment_name} size={72} />
                </Group>
                <Text size="sm" fw={700} mt={6} c={BRAND.textPrimary}>
                  {option.equipment_name}
                </Text>
              </Card>
            </UnstyledButton>
          ))}
        </SimpleGrid>
        {!gymEquipmentQuery.isLoading && equipmentOptions.length === 0 && (
          <Text size="sm" c={BRAND.textMuted} ta="center" py="md">
            Nenhum equipamento cadastrado para este grupo muscular nesta academia.
          </Text>
        )}
      </Stack>
    );
  }

  if (phase === 'pick-exercise') {
    return (
      <Stack gap="md" py="md">
        <Button variant="subtle" leftSection={<IconArrowLeft size={16} />} onClick={() => setPhase('pick-equipment')}>
          Voltar
        </Button>
        <Title order={3} c={BRAND.textPrimary}>
          {selectedEquipment?.equipment_name}
        </Title>
        <Text size="sm" c={BRAND.textMuted}>
          Passo 3 de 3 — escolha o exercício.
        </Text>
        <Stack gap="xs">
          {exerciseOptions.map((option) => (
            <Card
              key={option.link_id}
              component="button"
              withBorder
              radius="xl"
              p="md"
              onClick={() => !pickExerciseMutation.isPending && pickExerciseMutation.mutate(option)}
              style={{ cursor: 'pointer', textAlign: 'left', background: BRAND.bgElevated, borderColor: BRAND.border }}
            >
              <Group justify="space-between" wrap="nowrap">
                <div>
                  <Text fw={700} c={BRAND.textPrimary}>
                    {option.exercise_name}
                  </Text>
                  <PairStars stars={selectedEquipment ? pairStars.get(pairKey(selectedEquipment.equipment_id, option.mapping_id)) : undefined} />
                </div>
                {option.primary_group && (
                  <Badge size="sm" variant="light" color="brand">
                    {option.primary_group}
                  </Badge>
                )}
              </Group>
            </Card>
          ))}
          {exerciseOptions.length === 0 && (
            <Text size="sm" c={BRAND.textMuted} ta="center" py="md">
              Nenhum exercício cadastrado para este equipamento.
            </Text>
          )}
        </Stack>
        {pickExerciseMutation.isPending && <Loader size="sm" color="brand" />}
        {pickExerciseMutation.isError && (
          <Alert color="red" variant="light" icon={<IconAlertTriangle size={16} />}>
            {errorMessage(pickExerciseMutation.error, 'Não foi possível iniciar o exercício.')}
          </Alert>
        )}
      </Stack>
    );
  }

  if (phase === 'logging' && selectedExercise) {
    const nextSetNumber = loggedSets.length + 1;
    const lastSet = loggedSets[loggedSets.length - 1];
    // Só existe um exercício ativo por vez neste fluxo, então a sugestão
    // numérica da última avaliação sempre se refere à próxima série DESTE
    // exercício — sem precisar de uma chave de exercício.
    const suggestion = evaluation.result;
    // M22 — mesma carga que o `SetLoggerCard` vai oferecer na próxima série,
    // calculada UMA vez e reusada no descanso (`RestTimer`) para o número do
    // descanso não divergir do número do registro.
    // Lote T5: campos/valores iniciais pela medida escolhida (núcleo). Em
    // reps, os de sempre; fora, carga opcional sem sugestão.
    const measureDefaults = measureLoggerDefaults({
      measure: freeMeasure,
      repsTarget: suggested?.reps_target,
      lastSet,
      suggestedReps: suggestion?.suggested_next_reps ?? null,
    });
    const nextSetWeightKg = measureDefaults.loadOptional
      ? (lastSet?.weight_kg ?? null)
      : suggestion?.suggested_next_load_kg != null
        ? suggestion.suggested_next_load_kg
        : (lastSet?.weight_kg ?? suggested?.suggested_load_kg ?? parseNumericTarget(suggested?.load_target) ?? SET_LOGGER_FALLBACK_LOAD_KG);
    const nextSetPreview = resolveNextSetPreview({
      isExerciseTransition: false,
      stage: null,
      // Treino livre não tem meta de séries: o rótulo é só "Série N".
      workingSetNumber: nextSetNumber,
      workingSetTotal: 0,
      weightKg: nextSetWeightKg,
      measure: freeMeasure,
    });

    return (
      <Stack gap="md" py="md">
        <Group justify="space-between" wrap="nowrap">
          <Text size="sm" c={BRAND.textMuted} fw={600}>
            {loggedSets.length} série(s) lançada(s)
          </Text>
          <Button size="xs" variant="light" color="orange" onClick={finishExercise}>
            Encerrar exercício
          </Button>
        </Group>

        {resting ? (
          <>
            <RestTimer
              resetKey={`${sessionExerciseId}-${loggedSets.length}`}
              initialSeconds={DEFAULT_REST_SECONDS}
              onFinish={() => setResting(false)}
              nextSet={nextSetPreview}
              weightUnit={weightUnit}
            />
            {perSetAiEnabled && (
              <SetEvaluationCard loading={evaluation.loading} error={evaluation.error} result={evaluation.result} />
            )}
          </>
        ) : (
          <>
          <SegmentedControl
            fullWidth
            radius="xl"
            aria-label="Medida da série"
            value={freeMeasure}
            onChange={setFreeMeasure}
            // A medida vale para o exercício inteiro: trava depois da 1ª série.
            disabled={loggedSets.length > 0}
            data={MEASURE_OPTIONS.map((option) => ({ value: option.value, label: option.label }))}
          />
          <SetLoggerCard
            exerciseName={selectedExercise.exercise_name}
            equipmentName={selectedEquipment?.equipment_name}
            equipmentPhotoUrl={equipmentPhotoUrl}
            muscleGroup={selectedExercise.primary_group}
            muscleSecondaryGroup={selectedExercise.secondary_group}
            setLabel={`Série ${nextSetNumber}`}
            repsTarget={suggested?.reps_target}
            loadTarget={suggested?.load_target}
            rpeTarget={suggested?.rpe_target}
            defaultReps={measureDefaults.reps}
            defaultWeight={nextSetWeightKg}
            measure={freeMeasure}
            defaultDurationSeconds={measureDefaults.durationSeconds}
            // Teto final do par vem do servidor (segurança + recuperação): a tela
            // nunca OFERECE um RPE acima dele, nem repetindo o da última série.
            defaultRpe={clampRpeSuggestion(
              suggestion?.suggested_next_rpe != null
                ? suggestion.suggested_next_rpe
                : (lastSet?.rpe ?? parseNumericTarget(suggested?.rpe_target)),
              suggested?.effective_rpe_ceiling,
            )}
            previousSessionSets={previousSessionSetsQuery.isSuccess ? previousSessionSets : null}
            previousSessionRecord={previousSessionRecord}
            currentGroupFatigue={currentGroupFatigue}
            previousSessionHighlight={nextSetNumber}
            weightUnit={weightUnit}
            onWeightUnitChange={setWeightUnit}
            loadIncrementKg={suggested?.load_increment_kg ?? null}
            progressionNote={suggested?.progression_note}
            saving={saveSetMutation.isPending}
            saveLabel="Lançar série"
            onSave={(values) => saveSetMutation.mutate(values)}
            onFinishExercise={finishExercise}
          />
          </>
        )}

        {saveSetMutation.isError && (
          <Alert color="red" variant="light" icon={<IconAlertTriangle size={16} />}>
            {errorMessage(saveSetMutation.error, 'Não foi possível salvar a série.')}
          </Alert>
        )}
      </Stack>
    );
  }

  if (phase === 'exercise-done') {
    return (
      <Stack gap="md" py="xl" align="center">
        <IconCheck size={48} color="var(--mantine-color-green-6)" />
        <Title order={3} c={BRAND.textPrimary}>
          Exercício encerrado
        </Title>
        <Text c={BRAND.textMuted} ta="center">
          {selectedExercise?.exercise_name} — {loggedSets.length} série(s) registrada(s).
        </Text>
        <ExerciseSummaryCard
          loading={exerciseSummary.loading}
          error={exerciseSummary.error}
          result={exerciseSummary.result}
        />
        <Button size="lg" radius="xl" h={56} fullWidth onClick={startNewExercise}>
          Adicionar novo exercício
        </Button>
        {onReturnToPlan && (
          <Button size="lg" radius="xl" h={56} fullWidth variant="light" onClick={onReturnToPlan}>
            Voltar para a ficha
          </Button>
        )}
        <Button
          size="lg"
          radius="xl"
          h={56}
          fullWidth
          variant="light"
          color="green"
          leftSection={<IconPlayerStop size={18} />}
          loading={finishMutation.isPending}
          onClick={() => finishMutation.mutate()}
        >
          Encerrar treino
        </Button>
        {finishError}
      </Stack>
    );
  }

  return null;
}

// Nota do usuário para o par (só leitura); sem avaliação não mostra nada.
function PairStars({ stars }: { stars: number | undefined }) {
  if (stars == null) return null;
  return (
    <Group mt={4} aria-label={`Sua nota: ${starsLabel(stars)}`}>
      <Rating value={stars} count={MAX_PREFERENCE_STARS} size="xs" color="orange" readOnly />
    </Group>
  );
}
