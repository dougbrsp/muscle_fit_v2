'use client';

import { Alert, Badge, Button, Card, Group, Loader, Stack, Text, Title } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { notifications } from '@mantine/notifications';
import {
  IconAlertTriangle,
  IconArrowsExchange,
  IconCheck,
  IconCirclePlus,
  IconInfoCircle,
  IconPhoto,
  IconPlayerSkipForward,
  IconPlayerStop,
} from '@tabler/icons-react';
import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import { formatStrengthLoadRange } from '@/lib/exercise-briefing';
import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { apiFetch, ApiError } from '@/lib/api';
import {
  buildMuscleMapItems,
  clampRpeSuggestion,
  findLoggedExerciseForPlanItem,
  isPreparatorySetType,
  isSyntheticPlanExerciseId,
  nextPhysicalSetNumber,
  parseNumericTarget,
  type PreparatorySetType,
  type ActivityDetail,
  type AdvancedLocalStageProgress,
  type ApiList,
  type ExerciseSummaryResult,
  type GymEquipmentLink,
  type LastSessionSetsResponse,
  type MidExerciseExtra,
  type SetEvaluationResult,
  type StoredWorkoutModeSession,
  type SuggestedProgressionResponse,
  type SuggestedSetCountForSubstitutionResponse,
  type WarmupState,
  type WorkoutModeEquipmentOverride,
  type WorkoutModePlanExercise,
  type WorkoutPlanDetail,
} from '@/lib/workout-mode-types';
import { fetchWorkoutAiResult } from '@/lib/workout-mode-ai';
import { loadStoredSession, restoreBriefedKeys, saveStoredSession } from '@/lib/workout-mode-storage';
import {
  feelerSetLoad,
  techniqueApplicationLoad,
  warmupLoad,
  type SessionLoadContext,
} from '@/lib/workout-mode-load-suggestions';
import {
  collectCardThumbnailUrls,
  preloadImages,
  resolveExerciseCardSource,
  useEffectiveExerciseCard,
} from '@/lib/workout-mode-exercise-card';
import { buildSafetyEventBannerText, isSafetyAlertEvent, safetyEventColor } from '@/lib/intensity-status';
import { suggestedProgressionTargets } from '@/lib/suggested-progression';
import { resolveNextSetPreview } from '@/lib/workout-mode-next-set';
import {
  measureLoggerDefaults,
  measureSetLabel,
  setMeasureOf,
  setMeasurePayload,
} from '@/lib/workout-mode-measures';
import { setSidesPayload, withSavedCanonical } from '@/lib/set-sides';
import {
  advanceLocalStageProgress,
  advancedStageOf,
  effectiveAdvancedStageDone,
  hasAdvancedFlow as computeHasAdvancedFlow,
  remainingTechniqueApplications,
} from '@/lib/workout-mode-advanced-stage';
import { selectPreviousSessionRecord } from '@/lib/workout-mode-previous-session';
import { createSubmitLatch } from '@/lib/submit-latch';
import { selectPreviousSessionWorkingSets } from '@/lib/workout-mode-previous-session-sets';
import {
  buildExecutionQueue,
  findPlanItemForSubstitutedExercise,
  reorderPendingBySkip,
  buildSyntheticContinuation,
  executionPositionOrdinal,
  findPlanItemsCoveredByAnotherSlot,
  findRemainingAfterUpcoming,
  findUpcomingExercise,
  isAdvancedExerciseDone,
  originalPlanItemId,
  progressAfterEquipmentSwap,
  resolveEffectiveExerciseDisplay,
  resolveRestSeconds,
  resolveSessionPairLimits,
  shouldSwapAsOverride,
  syntheticExtraToPlanExercise,
  type AdvancedExerciseCounters,
  type AdvancedSubStage,
} from '@/lib/workout-mode-plan-flow';
import {
  isExtraRepsTechnique,
  getAdvancedTechniqueApplicationNoun,
  getAdvancedTechniqueDescription,
  getAdvancedTechniqueLabel,
  getAdvancedTechniqueSetType,
  type AdvancedTechniqueValue,
} from '@/lib/advanced-techniques';
import { useAdvancedTechniques } from '@/lib/use-advanced-techniques';
import { techniqueCatalogFromItems } from '@/lib/technique-accounting';
import {
  blockApplicationRows,
  countWorkingSets,
  describeAppliedRows,
  findAnchorWorkingSet,
  previewTechniqueApplication,
  techniqueGoalLabel,
  techniqueStageLabel,
  toTechniqueApplication,
} from '@/lib/workout-mode-technique';
import { finishWorkoutSession } from '@/lib/workout-session-finish';
import { BRAND } from '@/theme';
import { EquipmentSubstituteModal, type EquipmentSubstituteChoice } from './EquipmentSubstituteModal';
import { FinishExerciseModal } from './FinishExerciseModal';
import { FreeformScreen } from './FreeformScreen';
import { MuscleMapDisclosure } from './MuscleMapDisclosure';
import { LastExerciseNotice, NextExercisePreviewCard } from './NextExercisePreviewCard';
import { RemainingExercisesList, type RemainingExerciseRow } from './RemainingExercisesList';
import { ExerciseBriefingScreen } from './ExerciseBriefingScreen';
import { RestTimerHero } from './RestTimer';
import { RestAiEvaluationCard, RestSavedSetPanel } from './RestRecommendation';
import { useWeightUnit } from '@/lib/use-weight-unit';
import { formatWeight, type WeightUnit } from '@/lib/weight-units';
import { ExerciseSummaryCard, SetLoggerCard, type SetLoggerValues } from './SetLoggerCard';
import type { SavedSetIntensity } from '@/lib/set-engine-status';
import { restoreRestTimer, type RestAfterStageKind, type StoredRestTimer } from '@/lib/workout-mode-rest-timer';
import { restSavedSetView } from '@/lib/workout-mode-rest-view';
import { SkipExerciseModal, type SkipExerciseModalItem } from './SkipExerciseModal';
import { LaboralExerciseCard } from './LaboralExerciseCard';
import {
  isLaboralItemDone,
  isLaboralPlanExercise,
  laboralPrescriptionOf,
  laboralSetRecords,
} from '@/lib/workout-mode-laboral';
import {
  DEFAULT_REST_SECONDS,
  SET_LOGGER_FALLBACK_LOAD_KG,
} from '@/lib/prescription-defaults';
import { buildAdvancedExerciseCounters } from '@/lib/workout-mode-advanced-counters';
import { WorkoutModeNotice } from './WorkoutModeNotice';
import { newIdempotencyKey } from '@/lib/exercise-cards';
import { isRepBased } from '@/lib/set-measures';
import { isWorkingSetType } from '@/lib/set-types';
import {
  adjustApplicationLoad,
  intensiveApplicationKey,
  intensiveApplicationLabel,
  intensiveApplicationPayload,
  intensiveApplicationPlannedLoad,
  intensiveExtraRepsPayload,
  intensiveItemDone,
  intensiveLoadConventionSuffix,
  intensiveReducer,
  intensiveRestSeconds,
  intensiveSetPayload,
  isIntensiveBlockProfile,
  nextIntensiveSetNumber,
  reconcileIntensiveBlock,
  resolveIntensiveProfile,
  restoreIntensiveBlocks,
  undoApplicationLoad,
  type IntensiveApplicationAdjustment,
  type IntensiveBlockState,
  type IntensiveEvent,
  type IntensivePause,
  type IntensivePlannedLoad,
  type IntensiveProfile,
  type IntensiveServerSet,
  type IntensiveSetRecord,
  techniquePauseSeconds,
} from '@/lib/workout-mode-intensive';
import {
  collectPendingRepsSets,
  finishNeedsPendingConfirmation,
  type PendingRepsSet,
} from '@/lib/workout-mode-pending-reps';

import { TrainingGoalBadge } from '@/components/TrainingGoalBadge';
import { goalDefaultRestSeconds, techniqueForGoal, useTrainingGoals } from '@/lib/training-goals';
import { DeloadSessionBanner } from './DeloadPanels';
import {
  PAIR_SUGGESTION_QUERY_ROOT,
  pairSuggestionPath,
  workoutModePlanPath,
  workoutModePlanQueryKey,
} from '@/lib/deload';
import { IntensiveApplicationCard } from './IntensiveApplicationCard';
import { IntensiveBlock } from './IntensiveBlock';
import { PendingRepsFinishModal } from './PendingRepsFinishModal';
import type { IntensiveSetCorrection } from './IntensiveReview';
import type { WorkoutIconAction } from './ui/IconAction';
import { NextExerciseLine } from './ui/NextExerciseLine';
import { WorkoutOverallProgress } from './ui/WorkoutOverallProgress';
import { WORKOUT_MODE_CSS_VARS } from './ui/workout-mode-css-vars';
import workoutModeStyles from './workout-mode.module.css';
import {
  GENERIC_WARMUP_REPS,
  collectActivatedMuscleGroups,
  genericWarmupLoads,
  genericWarmupSet,
  needsGenericWarmup,
  resolveGenericWarmupState,
  shouldShowGenericWarmup,
} from '@/lib/workout-mode-generic-warmup';

type IntensiveServerSetsInput = ReadonlyArray<IntensiveServerSet>;

// Série preparatória (aquecimento/ajuste) gravada via
// `POST /workout-sessions/{id}/exercises/{eid}/sets`.
type PreparatorySetInput = {
  set_type: PreparatorySetType;
  reps: number | null;
  weight_kg: number | null;
  rpe?: number | null;
  muscle_pain?: number | null;
  joint_pain?: boolean | null;
  notes?: string | null;
};

type Override = WorkoutModeEquipmentOverride;

type ProgressItem = {
  planExercise: WorkoutModePlanExercise;
  loggedRow: ActivityDetail['exercises'][number] | undefined;
  loggedCount: number;
  // Só séries de TRABALHO — "Série N de S" (Fase 5: aplicações de técnica são
  // subitens da série âncora, nunca "Série N+1").
  loggedWorkingCount: number;
  targetCount: number;
  // Contadores do treino avançado (alvos zerados fora dele) — mesma base
  // para `done` e para a sub-etapa exibida (`resolveAdvancedSubStage`).
  advancedCounters: AdvancedExerciseCounters;
  done: boolean;
};

type EvaluationState = {
  loading: boolean;
  error: boolean;
  result: SetEvaluationResult | null;
  // Exercício para o qual `result` foi gerado — a sugestão numérica
  // (`suggested_next_*`) só pode virar default da PRÓXIMA série do MESMO
  // exercício, nunca de um exercício diferente.
  forKey: string | null;
};

type ExerciseSummaryState = { loading: boolean; error: boolean; result: ExerciseSummaryResult | null };

const IDLE_SUMMARY: ExerciseSummaryState = { loading: false, error: false, result: null };
const IDLE_EVALUATION: EvaluationState = { loading: false, error: false, result: null, forKey: null };

const SAVE_SET_ERROR = 'Não foi possível salvar a série.';
const SAVE_PREPARATORY_ERROR = 'Não foi possível salvar a série de aquecimento/ajuste.';

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback;
}

function exerciseKey(planExercise: WorkoutModePlanExercise | undefined | null): string | null {
  if (!planExercise) return null;
  if (planExercise.workout_plan_exercise_id != null) return `wpe-${planExercise.workout_plan_exercise_id}`;
  if (planExercise.mapping_id != null) return `mapping-${planExercise.mapping_id}`;
  return null;
}

// Indicador discreto do próximo exercício da ficha (uma linha), exibido fora
// do descanso (onde `NextExercisePreviewCard`, com foto/nome, já cobre o caso).
function NextExerciseIndicator({ nextItem }: { nextItem: ProgressItem | null }) {
  return (
    <NextExerciseLine
      name={nextItem ? nextItem.planExercise.exercise_name : null}
      muscle={nextItem?.planExercise.muscle_primary}
      testId="next-exercise-line"
    />
  );
}

/**
 * Etapa de aquecimento (2 séries, cargas do backend — `session_loads.py` —, `GENERIC_WARMUP_REPS` reps)
 * sugerida antes das séries de trabalho de um exercício cujo grupo muscular
 * primário ainda não foi ativado nesta sessão. "Concluí" grava a série no
 * backend com `set_type: 'warmup'` (fora de agregados/progressão); "Pular"
 * fica só no estado local — ver `WarmupState` em `workout-mode-types.ts`.
 */
function WarmupCard({
  exerciseName,
  equipmentName,
  equipmentPhotoUrl,
  referenceWorkingLoad,
  loadContext,
  weightUnit,
  state,
  saving,
  onMark,
  onSkipAll,
}: {
  exerciseName: string;
  equipmentName?: string | null;
  equipmentPhotoUrl?: string | null;
  referenceWorkingLoad: number;
  loadContext: SessionLoadContext;
  weightUnit: WeightUnit;
  state: WarmupState;
  saving: boolean;
  onMark: (which: 'first' | 'second', status: 'done' | 'skipped') => void;
  onSkipAll: () => void;
}) {
  const [firstWeight, secondWeight] = genericWarmupLoads(referenceWorkingLoad, loadContext);
  const rows: Array<{ key: 'first' | 'second'; label: string; weight: number }> = [
    { key: 'first', label: 'Aquecimento 1', weight: firstWeight },
    { key: 'second', label: 'Aquecimento 2', weight: secondWeight },
  ];

  return (
    <Card withBorder radius="xl" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
      <Stack gap="md">
        <Group gap="sm" wrap="nowrap" align="flex-start">
          {equipmentPhotoUrl ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={equipmentPhotoUrl}
              alt={equipmentName || exerciseName}
              width={56}
              height={56}
              style={{ objectFit: 'cover', borderRadius: 12, flexShrink: 0 }}
            />
          ) : (
            <IconPhoto size={56} color={BRAND.textMuted} style={{ flexShrink: 0 }} />
          )}
          <div>
            <Text fw={800} c={BRAND.textPrimary}>
              Aquecimento — {exerciseName}
            </Text>
            <Text size="xs" c={BRAND.textMuted}>
              Grupo muscular ainda não ativado nesta sessão. 2 séries de {GENERIC_WARMUP_REPS} repetições, opcionais.
              As concluídas são registradas como aquecimento e não contam como série de trabalho.
            </Text>
          </div>
        </Group>

        {rows.map((row) => {
          const status = state[row.key];
          return (
            <Group key={row.key} justify="space-between" wrap="nowrap">
              <div>
                <Text size="sm" fw={700} c={BRAND.textPrimary}>
                  {row.label}
                </Text>
                <Text size="xs" c={BRAND.textMuted}>
                  {formatWeight(row.weight, weightUnit, { space: false })} × {GENERIC_WARMUP_REPS} reps
                </Text>
              </div>
              {status === 'pending' ? (
                <Group gap="xs" wrap="nowrap">
                  <Button
                    size="sm"
                    radius="xl"
                    variant="light"
                    color="teal"
                    loading={saving}
                    onClick={() => onMark(row.key, 'done')}
                  >
                    Concluí
                  </Button>
                  <Button
                    size="sm"
                    radius="xl"
                    variant="subtle"
                    color="gray"
                    disabled={saving}
                    onClick={() => onMark(row.key, 'skipped')}
                  >
                    Pular
                  </Button>
                </Group>
              ) : (
                <Badge size="md" variant="light" color={status === 'done' ? 'teal' : 'gray'}>
                  {status === 'done' ? 'Concluída' : 'Pulada'}
                </Badge>
              )}
            </Group>
          );
        })}

        <Button size="sm" radius="xl" variant="subtle" color="gray" disabled={saving} onClick={onSkipAll}>
          Pular aquecimento
        </Button>
      </Stack>
    </Card>
  );
}

type Props = {
  session: StoredWorkoutModeSession;
  perSetAiEnabled: boolean;
  onFinish: (sessionId: number) => void;
  // Vaga na barra fina do topo (`WorkoutModeShell`) onde a linha de
  // progresso é desenhada (portal). Ausente: a linha fica no topo da tela.
  headerSlot?: HTMLElement | null;
};

/**
 * Execução guiada de um treino a partir de uma ficha (`workout_plan_id`
 * vinculado à sessão). A fila de exercícios é SEMPRE derivada do servidor —
 * `GET /workout-plans/{id}` (o que está previsto) cruzado com
 * `GET /activities/{session_id}` (o que já foi lançado, via
 * `findLoggedExerciseForPlanItem`) — nunca de um índice local acumulado, de
 * forma que fechar a aba no meio de uma série não perde o lugar.
 *
 * Desvios de fluxo suportados: pular (fim da fila) / cancelar (some da
 * sessão) via `SkipExerciseModal`; trocar de equipamento antes da 1ª série
 * (override, com meta de séries recalculada por
 * `GET /exercises/suggested-set-count-for-substitution`); e encerrar o
 * exercício no meio trocando de equipamento pelas séries restantes
 * (`FinishExerciseModal` → continuação sintética, ver `MidExerciseExtra`).
 *
 * `onFinish(sessionId)` só é chamado após `POST /workout-sessions/{id}/finish`
 * responder com sucesso — resumo de IA do treino e navegação são do
 * orquestrador (`WorkoutModeClient`).
 */
export function PlanExecutionScreen({ session, perSetAiEnabled, onFinish, headerSlot = null }: Props) {
  const queryClient = useQueryClient();
  // Catálogo de técnicas (GET /advanced-techniques). Os helpers de
  // `advanced-techniques.ts` são síncronos e usam o fallback local até a
  // resposta chegar — mesmo set_type/família para os 8 códigos do seed.
  const techniqueCatalogItems = useAdvancedTechniques().items;
  // M97 F3: objetivos (rótulos e técnicas fora da política, da API).
  const trainingGoals = useTrainingGoals().data;

  // Chaves de objeto viram string ao passar por JSON.stringify/parse
  // (localStorage) — normaliza de volta para number, a chave real usada por
  // `workout_plan_exercise_id` em todo o componente.
  const [overrides, setOverrides] = useState<Record<number, Override>>(() =>
    Object.fromEntries(Object.entries(session.equipment_overrides || {}).map(([k, v]) => [Number(k), v])),
  );
  const [warmup, setWarmup] = useState<Record<number, WarmupState>>(() =>
    Object.fromEntries(Object.entries(session.warmup_state || {}).map(([k, v]) => [Number(k), v])),
  );
  // Progresso local das sub-etapas de aquecimento/ajuste/técnica do modo
  // "Fazer treino avançado" (ver `AdvancedLocalStageProgress` em
  // `workout-mode-types.ts`) — inclui etapas puladas; as concluídas também
  // são gravadas no backend com `set_type` `warmup`/`feeler`.
  const [advancedProgress, setAdvancedProgress] = useState<Record<number, AdvancedLocalStageProgress>>(() =>
    Object.fromEntries(Object.entries(session.advanced_stage_progress || {}).map(([k, v]) => [Number(k), v])),
  );
  const [midExerciseExtras, setMidExerciseExtras] = useState<MidExerciseExtra[]>(session.mid_exercise_extras || []);
  // Modo Intensivo (FST-7): estado do bloco por `workout_plan_exercise_id`
  // (`workout-mode-intensive.ts`). Espelho síncrono no ref: as respostas do
  // POST chegam depois do render e gravam sobre o valor mais novo.
  const [intensiveBlocks, setIntensiveBlocks] = useState<Record<number, IntensiveBlockState>>(() =>
    restoreIntensiveBlocks(session.intensive_blocks),
  );
  const intensiveBlocksRef = useRef(intensiveBlocks);
  // Trava por série do bloco (toque duplo em "Concluir série") e o exercício
  // em criação (duas séries concluídas antes da 1ª resposta não criam dois).
  const intensiveConcludedRef = useRef(new Set<string>());
  const intensiveExerciseIdRef = useRef(new Map<string, Promise<number>>());
  // Modo Intensivo, etapas da técnica (drop set / rest-pause): ajuste da carga
  // da etapa atual e a pausa curta em curso. Locais e efêmeros: a etapa
  // gravada leva a carga e a origem; a pausa recarregada volta como "pronta".
  const [applicationAdjust, setApplicationAdjust] = useState<IntensiveApplicationAdjustment | null>(null);
  const [techniquePause, setTechniquePause] = useState<IntensivePause | null>(null);
  // "Finalizar treino" com séries de reps pendentes: confirmação antes do fim.
  const [pendingFinishOpen, setPendingFinishOpen] = useState(false);
  const [skippedIds, setSkippedIds] = useState<Set<number>>(() => new Set(session.skipped_exercise_ids || []));
  const [cancelledIds, setCancelledIds] = useState<Set<number>>(() => new Set(session.cancelled_exercise_ids || []));
  const [priorityId, setPriorityId] = useState<number | null>(session.jump_target_exercise_id ?? null);

  // Descanso em andamento salvo na sessão local (PR3/3): um reload no meio do
  // descanso volta para a MESMA tela, com o relógio no ponto certo (fim
  // absoluto `rest_ends_at`). Sessão sem o campo = sem descanso, como antes.
  const [restoredRest] = useState(() => restoreRestTimer(session.rest_timer));
  // Espelho síncrono do descanso persistido: `persistSession` grava SEMPRE o
  // valor atual (não o do render), e o relógio lê daqui ao montar.
  const restTimerRef = useRef<StoredRestTimer | null>(restoredRest);
  // true entre o início e o fim de um descanso. Síncrono: um callback tardio
  // do relógio (StrictMode, desmontagem) depois do fim não regrava o descanso.
  const restActiveRef = useRef(restoredRest != null);
  const [resting, setResting] = useState(restoredRest != null);
  const [restIsExerciseTransition, setRestIsExerciseTransition] = useState(restoredRest?.exercise_transition ?? false);
  // Etapa que abriu o descanso atual (aquecimento/ajuste/técnica); `null` =
  // série de trabalho. Define o intervalo do cronômetro (`resolveRestSeconds`).
  const [restAfterStageKind, setRestAfterStageKind] = useState<RestAfterStageKind | null>(
    restoredRest?.after_stage_kind ?? null,
  );
  // Chave (`exerciseKey`) do exercício cuja ÚLTIMA série abriu o descanso de
  // transição — referência do "próximo exercício" nessa tela, já que o
  // `current` ao vivo já avançou (ver `findUpcomingExercise`).
  const [transitionFromKey, setTransitionFromKey] = useState<string | null>(restoredRest?.transition_from_key ?? null);
  // Identidade do descanso atual (série/etapa que o abriu): reinicia o relógio
  // só quando um NOVO descanso começa (uma troca de equipamento no meio do
  // descanso não zera a contagem).
  const [restKey, setRestKey] = useState<string | null>(restoredRest?.rest_key ?? null);
  // A série que abriu o descanso foi gravada (chip "SÉRIE SALVA"); etapa
  // pulada não grava nada.
  const [restSetSaved, setRestSetSaved] = useState(restoredRest?.set_saved ?? false);
  const [extendMode, setExtendMode] = useState(false);
  // M4: exercicios cujo briefing ja foi lido NESTA sessao. Persistido em
  // `briefed_keys` (novo visual, PR1/3): sem isso, um reload depois de
  // "Iniciar exercicio" e antes da 1a serie (ex.: no aquecimento) reabria o
  // briefing. Sessao salva antes do campo carrega com o conjunto vazio, como
  // antes (`restoreBriefedKeys`).
  const [briefedKeys, setBriefedKeys] = useState<Set<string>>(() => restoreBriefedKeys(session));
  const [evaluation, setEvaluation] = useState<EvaluationState>(IDLE_EVALUATION);
  const [exerciseSummary, setExerciseSummary] = useState<ExerciseSummaryState>(IDLE_SUMMARY);
  // RPE 10 Lote 6/P9: status do motor da última série de trabalho salva (resposta
  // do servidor), para o descanso e o card da próxima série do mesmo exercício.
  const [lastSavedSet, setLastSavedSet] = useState<{ forKey: string | null; saved: SavedSetIntensity } | null>(() =>
    restoredRest?.saved_set ? { forKey: restoredRest.saved_set.for_key, saved: restoredRest.saved_set.saved } : null,
  );

  const [skipModalOpened, { open: openSkipModal, close: closeSkipModal }] = useDisclosure(false);
  const [skipTargetId, setSkipTargetId] = useState<number | null>(null);
  const [substituteOpen, { open: openSubstitute, close: closeSubstitute }] = useDisclosure(false);
  const [finishModalOpened, { open: openFinishModal, close: closeFinishModal }] = useDisclosure(false);
  // true enquanto o `EquipmentSubstituteModal` foi aberto pela opção "Trocar
  // de equipamento (séries restantes)" do `FinishExerciseModal` — muda o
  // `onSelect` (cria uma continuação sintética em vez de só um override).
  const [swapRemainingMode, setSwapRemainingMode] = useState(false);
  const [swappingEquipment, setSwappingEquipment] = useState(false);

  // Contador decrescente do `localId` de cada `MidExerciseExtra`. Começa bem
  // abaixo de -1 para nunca colidir com o fallback `?? -1` de
  // `reorderPendingBySkip`.
  const nextSyntheticIdRef = useRef(-1000);

  const activityQueryKey = ['activity-detail-workout-mode', session.session_id];
  const activityQuery = useQuery({
    queryKey: activityQueryKey,
    queryFn: () => apiFetch<ActivityDetail>(`/activities/${session.session_id}`),
    refetchOnMount: 'always',
  });

  const planQuery = useQuery({
    // Com `session_id`: numa sessão de deload a ficha já vem com a prescrição
    // de deload desde a 1ª série e sem técnica (dono `domain/deload.py`).
    queryKey: workoutModePlanQueryKey(session.workout_plan_id, session.session_id),
    queryFn: () => apiFetch<WorkoutPlanDetail>(workoutModePlanPath(Number(session.workout_plan_id), session.session_id)),
    enabled: Boolean(session.workout_plan_id),
  });

  const gymEquipmentQuery = useQuery({
    queryKey: ['workout-mode-plan-gym-equipment', session.gym_id],
    queryFn: () => apiFetch<ApiList<GymEquipmentLink>>(`/gym-equipment?gym_id=${session.gym_id}&active_only=true`),
    enabled: Boolean(session.gym_id),
  });

  const activity = activityQuery.data;
  const plan = planQuery.data;

  // Modo avançado da sessão: desde o PR B4 da Evolução ZEPP decidido pela
  // ficha ao iniciar (`resolveWorkoutModeFromPlan`, em `ModeSelectScreen`) e
  // gravado em `advanced_mode`; aqui só se LÊ o valor salvo, para o modo não
  // mudar no meio do treino. Mantém a ficha inteira na fila e ativa as
  // sub-etapas reais abaixo nos exercícios que têm configuração avançada.
  // false/ausente (sessão antiga) preserva o comportamento normal.
  const advancedMode = Boolean(session.advanced_mode);

  function persistSession(next: Partial<StoredWorkoutModeSession>) {
    saveStoredSession({
      ...session,
      skipped_exercise_ids: Array.from(skippedIds),
      cancelled_exercise_ids: Array.from(cancelledIds),
      jump_target_exercise_id: priorityId,
      equipment_overrides: overrides,
      warmup_state: warmup,
      mid_exercise_extras: midExerciseExtras,
      advanced_stage_progress: advancedProgress,
      briefed_keys: Array.from(briefedKeys),
      rest_timer: restTimerRef.current,
      intensive_blocks: intensiveBlocksRef.current,
      ...next,
    });
  }

  // Grava SÓ o estado dos blocos do Modo Intensivo sobre a sessão salva (mesmo
  // motivo de `persistRestTimer`: respostas assíncronas não desfazem o resto).
  function persistIntensiveBlocks(value: Record<number, IntensiveBlockState>) {
    intensiveBlocksRef.current = value;
    const stored = loadStoredSession();
    if (stored && stored.session_id === session.session_id) {
      saveStoredSession({ ...stored, intensive_blocks: value });
    }
  }

  // Pausa da etapa da técnica no fluxo normal: a MESMA do Intensivo (a da
  // prescrição, que traz o padrão do rest-pause do dono; senão a da ficha).
  function planTechniquePauseSeconds(planExercise: WorkoutModePlanExercise): number | null {
    return techniquePauseSeconds(
      planExercise.advanced_technique,
      planExercise.prescription?.advanced_technique_parameters ?? null,
      planExercise.advanced_technique_duration_seconds ?? null,
    );
  }

  // Perfil do Modo Intensivo do item (null = fluxo normal): técnica com tela
  // (FST-7), medida de repetições, séries da meta do item e descanso do item.
  function intensiveProfileFor(planExercise: WorkoutModePlanExercise, plannedSetsCount: number | null): IntensiveProfile | null {
    // M97 F3: item de força não recebe técnica que o objetivo proíbe
    // (`disallowed_techniques` do dono `training_safety`).
    const techniqueCode = techniqueForGoal(trainingGoals, planExercise.training_goal, planExercise.advanced_technique);
    if (!techniqueCode || !isRepBased(planExercise.measure)) return null;
    return resolveIntensiveProfile({
      techniqueCode,
      catalog: techniqueCatalogItems,
      parameters: planExercise.prescription?.advanced_technique_parameters ?? null,
      plannedSetsCount,
      itemRestSeconds:
        planExercise.rest_seconds ?? goalDefaultRestSeconds(trainingGoals, planExercise.training_goal, DEFAULT_REST_SECONDS),
      techniqueDurationSeconds: planExercise.advanced_technique_duration_seconds ?? null,
    });
  }

  // Perfil das ETAPAS da técnica (drop set / rest-pause) do item, ou null.
  function applicationProfileFor(planExercise: WorkoutModePlanExercise, plannedSetsCount: number | null): IntensiveProfile | null {
    const profile = intensiveProfileFor(planExercise, plannedSetsCount);
    return profile?.stageKind === 'application' ? profile : null;
  }

  // Início de um descanso (série salva ou etapa do avançado concluída/pulada).
  // O relógio grava o fim absoluto ao montar (`handleRestEndsAtChange`).
  function beginRest({
    key,
    isExerciseTransition,
    afterStageKind,
    fromKey,
    setSaved,
  }: {
    key: string;
    isExerciseTransition: boolean;
    afterStageKind: RestAfterStageKind | null;
    fromKey: string | null;
    setSaved: boolean;
  }) {
    restActiveRef.current = true;
    restTimerRef.current = null;
    setRestKey(`${key}@${Date.now()}`);
    setResting(true);
    setRestIsExerciseTransition(isExerciseTransition);
    setRestAfterStageKind(afterStageKind);
    setTransitionFromKey(fromKey);
    setRestSetSaved(setSaved);
  }

  // Grava SÓ o descanso na sessão local, sobre o que já está salvo: não usa a
  // base do render (`persistSession`), que num mesmo handler ainda teria
  // skips/cancelamentos antigos e desfaria a gravação feita logo antes. Sem
  // sessão salva desta mesma sessão (saiu pelo "X", treino encerrado), não
  // recria nada: o descanso fica só em memória.
  function persistRestTimer(value: StoredRestTimer | null) {
    restTimerRef.current = value;
    const stored = loadStoredSession();
    if (stored && stored.session_id === session.session_id) {
      saveStoredSession({ ...stored, rest_timer: value });
    }
  }

  // O relógio do descanso anunciou o fim absoluto (início ou ±15 s).
  function handleRestEndsAtChange(endsAt: number, totalSeconds: number) {
    if (!restActiveRef.current || restKey == null) return;
    persistRestTimer({
      rest_key: restKey,
      rest_ends_at: endsAt,
      total_seconds: totalSeconds,
      exercise_transition: restIsExerciseTransition,
      transition_from_key: transitionFromKey,
      after_stage_kind: restAfterStageKind,
      set_saved: restSetSaved,
      saved_set: lastSavedSet ? { for_key: lastSavedSet.forKey, saved: lastSavedSet.saved } : null,
    });
  }

  // Fim do descanso: some da sessão local (um reload depois não reabre).
  function endRest() {
    restActiveRef.current = false;
    persistRestTimer(null);
    setResting(false);
    setRestIsExerciseTransition(false);
    setTransitionFromKey(null);
  }

  // Ordem base = `sequence` da ficha, mais as continuações sintéticas
  // (sequence alto, mas a posição real de "próximo" vem sempre de
  // `priorityId`). Itens cancelados somem de vez desta sessão. Em
  // advancedMode a fila é a ficha inteira: exercícios sem configuração
  // avançada seguem com séries normais (ver `buildExecutionQueue`).
  const orderedExercises = useMemo<WorkoutModePlanExercise[]>(
    () =>
      buildExecutionQueue<WorkoutModePlanExercise>({
        planExercises: plan?.exercises || [],
        // Continuações sintéticas herdam a configuração avançada do item original.
        syntheticExercises: midExerciseExtras.map(syntheticExtraToPlanExercise),
        cancelledIds,
      }),
    [plan?.exercises, cancelledIds, midExerciseExtras],
  );

  // Cards ilustrados (PR5, §66 da especificação): pré-carrega as MINIATURAS
  // dos cards da ficha assim que ela chega, em segundo plano (nada espera
  // por isso). A imagem cheia só baixa ao abrir o "Como executar".
  useEffect(() => {
    preloadImages(collectCardThumbnailUrls(orderedExercises));
  }, [orderedExercises]);

  // Meta de séries ajustada por substituição de equipamento — uma query POR
  // override ativo (`useQueries`, não uma única presa ao exercício atual):
  // sem isso, o alvo ORIGINAL marcaria o exercício como concluído antes de o
  // alvo ajustado (maior, no caso comum de substituto mais fraco) ser
  // atingido. Só dispara quando o mapping_id do override difere do original.
  const overrideEntries = useMemo(() => Object.entries(overrides), [overrides]);
  const overrideSuggestionQueries = useQueries({
    queries: overrideEntries.map(([wpeIdStr, overrideValue]) => {
      const wpeId = Number(wpeIdStr);
      const planExercise = orderedExercises.find((ex) => ex.workout_plan_exercise_id === wpeId);
      const originalMappingId = planExercise?.mapping_id ?? null;
      const originalSetsCount = planExercise?.sets_count ?? null;
      return {
        queryKey: [
          'suggested-set-count-for-substitution',
          wpeId,
          originalMappingId,
          originalSetsCount,
          overrideValue.mapping_id,
        ],
        queryFn: () =>
          apiFetch<SuggestedSetCountForSubstitutionResponse>(
            `/exercises/suggested-set-count-for-substitution?original_mapping_id=${originalMappingId}` +
              `&original_sets_count=${originalSetsCount}&new_mapping_id=${overrideValue.mapping_id}`,
          ),
        enabled: Boolean(
          originalMappingId &&
            originalSetsCount &&
            overrideValue.mapping_id &&
            overrideValue.mapping_id !== originalMappingId,
        ),
      };
    }),
  });

  // Recalculado a cada render (barato — poucos overrides por sessão): não usa
  // useMemo porque cada objeto de resultado do useQueries muda de referência
  // a cada render mesmo sem `data` novo.
  const overrideAdjustedSetsCount: Record<number, number> = {};
  overrideEntries.forEach(([wpeIdStr], index) => {
    const data = overrideSuggestionQueries[index]?.data;
    if (data?.suggested_sets_count != null) {
      overrideAdjustedSetsCount[Number(wpeIdStr)] = data.suggested_sets_count;
    }
  });

  const progress = useMemo(() => {
    // Itens da ficha cujo exercício já foi executado sob OUTRO item, por
    // troca de exercício (ver `findPlanItemsCoveredByAnotherSlot`): ficam
    // concluídos e não voltam à fila, senão o usuário é mandado repetir um
    // exercício que já fez e o volume é contado duas vezes.
    const coveredByAnotherSlot = findPlanItemsCoveredByAnotherSlot({
      planExercises: orderedExercises,
      loggedExercises: activity?.exercises || [],
    });
    const base: ProgressItem[] = orderedExercises.map((planExercise) => {
      const loggedRow = findLoggedExerciseForPlanItem(activity, planExercise);
      // `loggedCount` conta só séries de TRABALHO e aplicações de técnica
      // gravadas no backend. Aquecimento/ajuste (`warmup`/`feeler`) também
      // são gravados, mas NÃO entram aqui — mantém "Série N de S", o alvo de
      // séries de trabalho e a conclusão do exercício coerentes.
      const loggedSets = loggedRow?.sets || [];
      const wpeId = planExercise.workout_plan_exercise_id;
      // Alvos e progresso do item (séries de trabalho, aquecimento/ajuste do
      // avançado e aplicações da técnica): núcleo do Modo Treino
      // (`buildAdvancedExerciseCounters`), o mesmo que o relógio consome.
      // `loggedNonPreparatory` não conta aquecimento/ajuste ("Série N de S").
      const advancedCounters: AdvancedExerciseCounters = buildAdvancedExerciseCounters({
        planExercise,
        loggedSets,
        advancedMode,
        adjustedSetsCount: wpeId != null ? overrideAdjustedSetsCount[wpeId] : undefined,
        localProgress: wpeId != null ? advancedProgress[wpeId] : undefined,
      });
      const loggedCount = advancedCounters.loggedNonPreparatory;
      const workingSetsTarget = advancedCounters.workingTarget;
      const countersDone = isAdvancedExerciseDone(advancedCounters);
      // Modo Intensivo: o bloco ativo segura o item até a revisão; "Encerrar
      // técnica" conclui antes da última série (`intensiveItemDone`).
      // Etapas de drop set/rest-pause seguem os contadores da Fase 5 (sem bloco).
      const intensiveProfile = wpeId != null ? intensiveProfileFor(planExercise, workingSetsTarget) : null;
      const itemDone =
        intensiveProfile && isIntensiveBlockProfile(intensiveProfile) && wpeId != null
          ? intensiveItemDone({ stored: intensiveBlocks[wpeId], countersDone, serverSets: loggedSets })
          : countersDone;
      return {
        planExercise,
        loggedRow,
        loggedCount,
        loggedWorkingCount: countWorkingSets(loggedSets),
        targetCount: workingSetsTarget,
        advancedCounters,
        // Mesma regra nos dois modos: fora do avançado os alvos de
        // aquecimento/ajuste são 0, então sem técnica é `loggedCount >= alvo`.
        // M100: o laboral fica concluído quando suas séries `laboral` estão no
        // servidor ("Concluir"); elas não entram em `loggedNonPreparatory`.
        done:
          (wpeId != null && coveredByAnotherSlot.has(wpeId)) ||
          (isLaboralPlanExercise(planExercise) ? isLaboralItemDone(loggedSets) : itemDone),
      };
    });
    return reorderPendingBySkip(base, (item) => item.planExercise.workout_plan_exercise_id ?? -1, skippedIds, priorityId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orderedExercises, activity, skippedIds, priorityId, JSON.stringify(overrideAdjustedSetsCount), advancedMode, advancedProgress, techniqueCatalogItems, intensiveBlocks]);

  const currentIndex = progress.findIndex((item) => !item.done);
  const current = currentIndex >= 0 ? progress[currentIndex] : null;
  const completedCount = progress.filter((item) => item.done).length;
  // "Exercício N de T": N é o ordinal do exercício em execução (concluídos +
  // 1), não a posição do item na ficha — ver `executionPositionOrdinal`.
  const positionLabel = `Exercício ${executionPositionOrdinal(progress)} de ${orderedExercises.length}`;
  const currentKey = exerciseKey(current?.planExercise);

  // `priorityId` só vale enquanto o item escolhido ainda está pendente; uma
  // vez concluído/cancelado, a escolha manual é limpa para não vazar para a
  // sequência seguinte.
  useEffect(() => {
    if (priorityId == null) return;
    const stillPending = progress.some(
      (item) => !item.done && item.planExercise.workout_plan_exercise_id === priorityId,
    );
    if (!stillPending) {
      setPriorityId(null);
      persistSession({ jump_target_exercise_id: null });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [progress, priorityId]);

  // Na transição entre exercícios o `current` (derivado do servidor, refeito
  // ANTES de abrir o descanso) JÁ é o próximo exercício — o "próximo" exibido
  // é o primeiro pendente diferente do exercício recém-encerrado, e não o
  // pendente depois do `current` (isso mostrava o exercício seguinte ao
  // próximo). Fora da transição, exclui o próprio exercício atual.
  const upcomingExcludeKey = resting && restIsExerciseTransition ? transitionFromKey : currentKey;
  const nextPendingItem = useMemo(() => {
    if (currentIndex < 0) return null;
    return findUpcomingExercise(progress, (item) => exerciseKey(item.planExercise), upcomingExcludeKey);
  }, [progress, currentIndex, upcomingExcludeKey]);
  // M64: exercícios depois do próximo, na mesma fila/ordem de
  // `nextPendingItem` — listados abaixo do card "A seguir" na transição.
  const remainingExerciseRows = useMemo<RemainingExerciseRow[]>(() => {
    if (currentIndex < 0) return [];
    return findRemainingAfterUpcoming(progress, (item) => exerciseKey(item.planExercise), upcomingExcludeKey).map(
      ({ item, ordinal }) => ({
        key: exerciseKey(item.planExercise) ?? `ordinal-${ordinal}`,
        ordinal,
        name: item.planExercise.exercise_name,
        equipmentName: item.planExercise.equipment_name,
        setsCount: item.targetCount,
      }),
    );
  }, [progress, currentIndex, upcomingExcludeKey]);

  const currentWorkoutPlanExerciseId = current?.planExercise.workout_plan_exercise_id ?? null;
  const override =
    currentWorkoutPlanExerciseId != null ? overrides[currentWorkoutPlanExerciseId] : undefined;

  const effectiveMappingId = override?.mapping_id ?? current?.planExercise.mapping_id ?? null;
  // M102: E/D só no exercício unilateral DA FICHA (troca de exercício = valor único).
  const currentIsUnilateral =
    Boolean(current?.planExercise.is_unilateral) && effectiveMappingId === (current?.planExercise.mapping_id ?? null);
  // Prioridade: override em memória (UX imediata antes da 1ª série) > o que
  // já foi persistido no backend para este exercício (fonte de verdade ao
  // retomar uma sessão) > alvo original da ficha.
  const effectiveEquipmentLinkId =
    override?.equipment_link_id ??
    current?.loggedRow?.equipment_id ??
    current?.planExercise.preferred_equipment_id ??
    null;
  const effectiveEquipmentName =
    override?.equipment_name || current?.loggedRow?.equipment_name || current?.planExercise.equipment_name || null;

  // M4: a tela de briefing entra ENTRE a lista e a execucao - antes do
  // aquecimento e da primeira serie, e so uma vez por exercicio. Fica de fora
  // de exercicios sinteticos (continuacoes criadas dentro da sessao, que nao
  // tem linha de ficha para consultar) e de sessao sem ficha.
  const briefingPlanId = session.workout_plan_id ?? null;
  // M100: exercício laboral = card só leitura (sem briefing, aquecimento,
  // técnica ou registro por série).
  const currentLaboral = laboralPrescriptionOf(current?.planExercise);
  const showBriefing =
    currentLaboral == null &&
    briefingPlanId != null &&
    current != null &&
    currentKey != null &&
    !briefedKeys.has(currentKey) &&
    currentWorkoutPlanExerciseId != null &&
    !isSyntheticPlanExerciseId(currentWorkoutPlanExerciseId) &&
    current.loggedCount === 0;
  // D13 (caminho b) / PR-12: `requires_user_choice` marca um exercício cujo
  // equipamento NÃO pôde ser resolvido nesta academia ao congelar a
  // prescrição (`preferred_equipment_id` veio null). Enquanto não houver um
  // equipamento efetivo — override escolhido nesta sessão ou o que já foi
  // gravado numa série — o exercício não pode ser iniciado. Exercícios sem o
  // flag seguem o fluxo normal, intocado.
  const needsEquipmentChoice =
    Boolean(current?.planExercise.requires_user_choice) && effectiveEquipmentLinkId == null;
  // Nome e descrição do exercício EFETIVO (override > já lançado > ficha):
  // após trocar de exercício, a descrição acompanha o exercício novo.
  const effectiveExerciseDisplay = current
    ? resolveEffectiveExerciseDisplay({
        planExercise: current.planExercise,
        override,
        loggedRow: current.loggedRow,
        gymLinks: gymEquipmentQuery.data?.items,
        equipmentLinkId: effectiveEquipmentLinkId,
      })
    : null;
  const effectiveExerciseName = effectiveExerciseDisplay?.exerciseName || '';
  const effectiveExerciseDescription = effectiveExerciseDisplay?.description ?? null;
  // Card ilustrado do exercício EFETIVO (PR5): o da ficha enquanto o
  // exercício for o do item (troca só de equipamento mantém o card); trocou o
  // exercício -> `GET /exercises/{id}/card` do novo. Sem card -> null e nada
  // aparece no `SetLoggerCard`/briefing.
  const exerciseCardSource = current
    ? resolveExerciseCardSource({ planExercise: current.planExercise, override, loggedRow: current.loggedRow })
    : null;
  const effectiveExerciseCard = useEffectiveExerciseCard(exerciseCardSource);

  // Com equipamento substituído, os alvos da ficha original não valem mais —
  // busca a sugestão do motor v2 para o PAR NOVO (mesmo endpoint de
  // `/planos-treino` ao adicionar exercício manualmente). `equipment_id`
  // (= `gym_equipment.id`) escolhe a trilha do par; a resposta já traz o teto
  // final (segurança + recuperação projetada) e o passo do equipamento.
  // M97-D2: com a sessão e o item trocado, o par novo sai no objetivo do item
  // e, numa sessão de deload, já com a prescrição de deload desde a 1ª série.
  const currentItemGoal = current?.planExercise.training_goal ?? null;
  const currentPlanItemId = originalPlanItemId(currentWorkoutPlanExerciseId, midExerciseExtras);
  const suggestedProgressionQuery = useQuery({
    queryKey: [
      PAIR_SUGGESTION_QUERY_ROOT,
      effectiveMappingId,
      effectiveEquipmentLinkId,
      session.session_id,
      currentPlanItemId,
      currentItemGoal,
    ],
    queryFn: () =>
      apiFetch<SuggestedProgressionResponse>(
        pairSuggestionPath({
          mappingId: Number(effectiveMappingId),
          equipmentLinkId: effectiveEquipmentLinkId || null,
          sessionId: session.session_id,
          workoutPlanExerciseId: currentPlanItemId,
          trainingGoal: currentItemGoal,
        }),
      ),
    enabled: Boolean(override) && Boolean(effectiveMappingId),
  });
  // Alvos do par novo em texto (null da API vira '' — SKIP não tem faixa).
  const overrideTargets = suggestedProgressionQuery.data
    ? suggestedProgressionTargets(suggestedProgressionQuery.data)
    : null;
  const effectiveRepsTarget = override
    ? (overrideTargets?.reps_target ?? current?.planExercise.reps_target ?? null)
    : (current?.planExercise.reps_target ?? null);
  const effectiveLoadTarget = override
    ? (overrideTargets?.load_target ?? current?.planExercise.load_target ?? null)
    : (current?.planExercise.load_target ?? null);
  // Prescrição CONGELADA (PR-11) já com o overlay de segurança de hoje
  // aplicado pelo servidor — é ela, e nunca a linha crua da ficha, que diz o
  // alvo de RPE e o teto que valem nesta execução (PR-14, §5.7: onde a falha
  // não é permitida, a ficha nunca mostra 10). Com o par trocado nesta sessão
  // (override ou continuação sintética), ela deixa de valer.
  const syntheticExtra = isSyntheticPlanExerciseId(currentWorkoutPlanExerciseId)
    ? (midExerciseExtras.find((extra) => extra.localId === currentWorkoutPlanExerciseId) ?? null)
    : null;
  const pairChanged = Boolean(override) || syntheticExtra != null;
  const prescription = pairChanged ? null : (current?.planExercise.prescription ?? null);
  const prescribedEffectiveRpe = prescription?.effective_rpe_target ?? null;
  // M76 (C5/C7): teto e passo do par vêm prontos do servidor — sem `min` na
  // tela. Ver `resolveSessionPairLimits`.
  const pairLimits = current
    ? resolveSessionPairLimits({
        planExercise: current.planExercise,
        hasOverride: Boolean(override),
        pairSuggestion: suggestedProgressionQuery.data,
        syntheticExtra,
      })
    : null;
  const effectiveRpeCeiling = pairLimits?.rpeCeiling ?? null;
  const loadIncrementKg = pairLimits?.loadIncrementKg ?? null;
  // Cargas de aquecimento/ajuste/técnica: as da API quando a âncora é a
  // prescrita; espelho do backend para âncora dinâmica (C7).
  const sessionLoadContext: SessionLoadContext = { plan: pairLimits?.loadPlan ?? null, step: loadIncrementKg };
  const effectiveRpeTarget = override
    ? (overrideTargets?.rpe_target ?? current?.planExercise.rpe_target ?? null)
    : (prescribedEffectiveRpe != null
        ? String(prescribedEffectiveRpe)
        : (current?.planExercise.rpe_target ?? null));

  const previousSessionSetsQuery = useQuery({
    queryKey: ['last-session-sets', effectiveMappingId, session.session_id, effectiveEquipmentLinkId],
    queryFn: () =>
      apiFetch<LastSessionSetsResponse>(
        `/exercises/last-session-sets?mapping_id=${effectiveMappingId}&exclude_activity_id=${session.session_id}` +
          (effectiveEquipmentLinkId ? `&equipment_id=${effectiveEquipmentLinkId}` : ''),
      ),
    enabled: Boolean(effectiveMappingId),
  });
  // M21: a sessão anterior INTEIRA (só séries de trabalho), exibida no card.
  // A posição no array é a numeração mostrada — o `set_number` físico da
  // sessão anterior pode estar deslocado por aquecimento/ajuste gravados.
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
  // backend), num chip único no hero do briefing e da execução. Enquanto a
  // consulta carrega, `undefined` esconde o chip; sem leitura ou com erro na
  // consulta, "Fadiga indisponível".
  const currentGroupFatigue = previousSessionSetsQuery.isSuccess
    ? (previousSessionSetsQuery.data?.current_group_fatigue ?? null)
    : previousSessionSetsQuery.isError
      ? null
      : undefined;

  // M3: unidade em que ESTE usuário anota/lê a carga (kg por padrão). Só
  // atravessa a tela até os componentes de apresentação — o que é enviado ao
  // backend continua sendo kg, convertido dentro do `SetLoggerCard`.
  const { unit: weightUnit, setUnit: setWeightUnit } = useWeightUnit();

  // Foto do equipamento atual: quando o exercício já foi lançado nesta sessão
  // e não há override em memória, usa direto os campos já resolvidos pelo
  // backend em `GET /activities/{id}` (mais robusto do que procurar o link no
  // catálogo, que pode ter sido desativado). `photo_url` já vem pronto do
  // backend (arquivo estático via CloudFront + S3) — sem hook de resolução.
  const currentEquipmentPhotoUrl = useMemo(() => {
    if (!override && current?.loggedRow?.equipment_id) {
      return current.loggedRow.equipment_photo_url ?? null;
    }
    if (!effectiveEquipmentLinkId) return null;
    const link = (gymEquipmentQuery.data?.items || []).find((item) => item.id === effectiveEquipmentLinkId);
    return link?.photo_url ?? null;
  }, [gymEquipmentQuery.data, effectiveEquipmentLinkId, override, current?.loggedRow]);

  // Foto do PRÓXIMO item da ficha, para o `NextExercisePreviewCard` durante a
  // transição entre exercícios (resolvida pelo link de equipamento preferido
  // da ficha — o próximo exercício ainda não foi lançado, então não há
  // `loggedRow` de onde tirar `equipment_catalog_id`).
  const nextEquipmentLink = useMemo(() => {
    const preferredId = nextPendingItem?.planExercise.preferred_equipment_id;
    if (!preferredId) return null;
    return (gymEquipmentQuery.data?.items || []).find((item) => item.id === preferredId) ?? null;
  }, [nextPendingItem, gymEquipmentQuery.data]);
  const nextEquipmentPhotoUrl = nextEquipmentLink?.photo_url ?? null;

  const skipCandidates = useMemo(
    () =>
      progress.filter(
        (item) =>
          !item.done &&
          item.planExercise.workout_plan_exercise_id != null &&
          item.planExercise.workout_plan_exercise_id !== skipTargetId,
      ),
    [progress, skipTargetId],
  );

  const skipModalItems: SkipExerciseModalItem[] = useMemo(
    () =>
      skipCandidates.map((item) => {
        const link = (gymEquipmentQuery.data?.items || []).find(
          (candidate) => candidate.id === item.planExercise.preferred_equipment_id,
        );
        return {
          workoutPlanExerciseId: item.planExercise.workout_plan_exercise_id as number,
          exerciseName: item.planExercise.exercise_name,
          muscleGroup: item.planExercise.muscle_primary,
          equipmentName: item.planExercise.equipment_name,
          equipmentPhotoUrl: link?.photo_url ?? null,
        };
      }),
    [skipCandidates, gymEquipmentQuery.data],
  );

  // ── Aquecimento genérico (concluídos gravados como `warmup`) ────────────
  // Regra no núcleo do Modo Treino (`workout-mode-generic-warmup.ts`,
  // compartilhada com o relógio): grupo primário ainda não ativado, estado
  // local reconciliado com os aquecimentos já gravados (não duplica ao
  // retomar sem o localStorage) e, em advancedMode, substituído pelo
  // aquecimento REAL da ficha (ver `advancedStage` abaixo).
  // M100: o laboral não ativa grupo nenhum (não dispensa o aquecimento genérico).
  const activatedMuscleGroups = useMemo(
    () => collectActivatedMuscleGroups(progress.filter((item) => !isLaboralPlanExercise(item.planExercise))),
    [progress],
  );

  // Lote T5 (medidas de série): medida do item atual (prescrição congelada >
  // linha da ficha; ausente = reps). Fora de reps: cronômetro de execução,
  // carga opcional, sem aquecimento genérico nem sugestão de carga.
  const measureSpec = setMeasureOf(current?.planExercise);

  const needsWarmup = needsGenericWarmup(current?.planExercise.muscle_primary, activatedMuscleGroups);
  const currentWarmupState: WarmupState = resolveGenericWarmupState({
    workoutPlanExerciseId: currentWorkoutPlanExerciseId,
    local: currentWorkoutPlanExerciseId != null ? warmup[currentWorkoutPlanExerciseId] : undefined,
    loggedSets: current?.loggedRow?.sets || [],
  });
  const showWarmup = shouldShowGenericWarmup({
    advancedMode,
    measure: measureSpec.measure,
    hasCurrent: Boolean(current),
    loggedCount: current?.loggedCount ?? 0,
    workoutPlanExerciseId: currentWorkoutPlanExerciseId,
    needsWarmup,
    warmupState: currentWarmupState,
  });

  // Atualização funcional (lê o estado mais recente): também é chamada no
  // `onSuccess` assíncrono do POST do aquecimento.
  function setWarmupEntry(workoutPlanExerciseId: number, buildNext: (prev: WarmupState) => WarmupState) {
    setWarmup((curr) => {
      const prev = curr[workoutPlanExerciseId] ?? { first: 'pending', second: 'pending' };
      const next = { ...curr, [workoutPlanExerciseId]: buildNext(prev) };
      persistSession({ warmup_state: next });
      return next;
    });
  }

  function updateWarmup(nextState: WarmupState) {
    if (currentWorkoutPlanExerciseId == null) return;
    setWarmupEntry(currentWorkoutPlanExerciseId, () => nextState);
  }

  // "Concluí" grava a série de aquecimento (carga 20%/40% da referência de
  // trabalho, `GENERIC_WARMUP_REPS` reps) e só então marca como concluída localmente; "Pular"
  // continua só local.
  function handleWarmupMark(which: 'first' | 'second', status: 'done' | 'skipped') {
    if (currentWorkoutPlanExerciseId == null) return;
    if (status === 'skipped') {
      updateWarmup({ ...currentWarmupState, [which]: 'skipped' });
      return;
    }
    if (savePreparatorySetMutation.isPending || !savePreparatoryLatchRef.current.tryEnter()) return;
    const workoutPlanExerciseId = currentWorkoutPlanExerciseId;
    // Mesma referência exibida no `WarmupCard` (`referenceWorkingLoad`).
    // `repsDefaultWeight` é declarado mais abaixo no render; esta função só
    // roda em clique, depois do render completo (aquecimento genérico só
    // existe em item de repetições).
    const referenceLoad = repsDefaultWeight;
    savePreparatorySetMutation.mutate(
      genericWarmupSet(which, referenceLoad, sessionLoadContext),
      { onSuccess: () => setWarmupEntry(workoutPlanExerciseId, (prev) => ({ ...prev, [which]: 'done' })) },
    );
  }

  // ── Fazer treino avançado: sub-etapas reais (aquecimento -> ajuste ->
  // trabalho -> aplicações da técnica) ────────────────────────────────────
  // Fase 5: a técnica vale nos dois modos (a sub-etapa `technique` deixou de
  // exigir `advancedMode`); aquecimento/ajuste continuam só no avançado.
  const advancedTechniqueValue: AdvancedTechniqueValue | null =
    (techniqueForGoal(
      trainingGoals,
      current?.planExercise.training_goal,
      current?.planExercise.advanced_technique,
    ) as AdvancedTechniqueValue | null) ?? null;
  const advancedLoggedSets = current?.loggedRow?.sets || [];
  const advancedApplicationsTotal = current?.planExercise.advanced_technique_applications || 0;
  const currentAdvancedProgress =
    currentWorkoutPlanExerciseId != null ? advancedProgress[currentWorkoutPlanExerciseId] : undefined;
  // Progresso efetivo = maior entre o contador local (inclui puladas) e as
  // séries já gravadas no backend — ver `AdvancedLocalStageProgress`.
  const { warmupDone: advancedWarmupDoneCount, feelerDone: advancedFeelerDoneCount } = effectiveAdvancedStageDone({
    localProgress: currentAdvancedProgress,
    loggedSets: advancedLoggedSets,
  });
  // Aquecimento/ajuste são recursos independentes da técnica avançada — um
  // exercício só com aquecimento/ajuste, sem técnica, ainda passa por essas
  // sub-etapas. A sub-etapa `technique` continua condicionada a
  // `advancedTechniqueValue` logo abaixo. Regra no núcleo do Modo Treino
  // (`workout-mode-advanced-stage.ts`, PR B3 da Evolução ZEPP).
  const hasAdvancedFlow = computeHasAdvancedFlow({ advancedMode, planExercise: current?.planExercise });
  const advancedStage: AdvancedSubStage | null = advancedStageOf({
    advancedMode,
    planExercise: current?.planExercise,
    counters: current?.advancedCounters,
  });

  // Avança o contador LOCAL da sub-etapa (`advancedProgress`) e abre a tela
  // de descanso, como `saveSetMutation.onSuccess` faz para uma série real —
  // sem avaliação de IA por série nem resumo de fim de exercício. Chamada
  // para "Pular" (nada gravado) e, para aquecimento/ajuste CONCLUÍDOS, no
  // `onSuccess` de `savePreparatorySetMutation` (série já gravada). O novo
  // valor parte do progresso EFETIVO capturado no clique
  // (`advancedWarmupDoneCount`/`advancedFeelerDoneCount`, que já considera o
  // que está gravado) + 1 — não duplica a contagem quando o refetch já trouxe
  // a série recém-gravada. Também usada para "Pular" uma aplicação da
  // técnica (`kind: 'technique'`), que só incrementa `techniqueSkipped`.
  // `count` > 1 só para "Encerrar técnica": marca de uma vez as aplicações
  // restantes como puladas (menos mini-blocos que o prescrito).
  // `saved` = a etapa foi gravada no backend (aquecimento/ajuste concluído):
  // o descanso mostra "SÉRIE SALVA"; pular não grava nada.
  // `skipRest`: etapas do Modo Intensivo (drop set / rest-pause) não abrem o
  // descanso entre etapas; só a conclusão do exercício abre a transição.
  function advanceLocalAdvancedStage(
    kind: 'warmup' | 'feeler' | 'technique',
    count = 1,
    saved = false,
    options: { skipRest?: boolean } = {},
  ) {
    if (currentWorkoutPlanExerciseId == null) return;
    const effectiveWarmupDone = advancedWarmupDoneCount;
    const effectiveFeelerDone = advancedFeelerDoneCount;
    setAdvancedProgress((curr) => {
      const nextEntry = advanceLocalStageProgress({
        existing: curr[currentWorkoutPlanExerciseId],
        kind,
        effectiveDone: { warmupDone: effectiveWarmupDone, feelerDone: effectiveFeelerDone },
        count,
      });
      const next = { ...curr, [currentWorkoutPlanExerciseId]: nextEntry };
      persistSession({ advanced_stage_progress: next });
      return next;
    });
    setEvaluation(IDLE_EVALUATION);
    setLastSavedSet(null);
    // Pular a ÚLTIMA aplicação pendente conclui o exercício: o descanso vira
    // transição de exercício (resumo + preview do PRÓXIMO, excluindo o
    // recém-encerrado — no próximo render `current` já é o exercício
    // seguinte). Sem próximo pendente a ficha acabou: vai direto para
    // "Ficha concluída!", sem descanso pendurado.
    const completesExercise =
      kind === 'technique' &&
      current != null &&
      isAdvancedExerciseDone({
        ...current.advancedCounters,
        techniqueSkipped: current.advancedCounters.techniqueSkipped + count,
      });
    if (completesExercise && current?.loggedRow) {
      runExerciseSummary(current.loggedRow.id);
    } else {
      setExerciseSummary(IDLE_SUMMARY);
    }
    if (!completesExercise && options.skipRest) return;
    if (!completesExercise || nextPendingItem != null) {
      beginRest({
        key: `${currentKey}#${kind}-${count}`,
        isExerciseTransition: completesExercise,
        afterStageKind: kind,
        fromKey: completesExercise ? currentKey : null,
        setSaved: saved,
      });
    } else {
      endRest();
    }
  }

  // ── Lançamento de séries ────────────────────────────────────────────────
  const ensureExerciseMutation = useMutation({
    mutationFn: async () => {
      if (!current) throw new ApiError('Nenhum exercício atual.', 0);
      if (current.loggedRow) return current.loggedRow.id;

      const planItemId = current.planExercise.workout_plan_exercise_id;
      // Ids sintéticos (negativos) não existem em `workout_plan_exercises` —
      // envia o id ORIGINAL que a continuação substitui, senão o exercício
      // fica órfão da ficha (quebra "Já lançado" em `WorkoutDetailClient`).
      const workoutPlanExerciseId =
        planItemId != null && !isSyntheticPlanExerciseId(planItemId)
          ? planItemId
          : (midExerciseExtras.find((extra) => extra.localId === planItemId)?.originalWorkoutPlanExerciseId ?? null);

      const created = await apiFetch<{ exercise_id: number }>(`/workout-sessions/${session.session_id}/exercises`, {
        method: 'POST',
        body: JSON.stringify({
          original_name: effectiveExerciseName,
          mapping_id: effectiveMappingId,
          equipment_id: effectiveEquipmentLinkId,
          workout_plan_exercise_id: workoutPlanExerciseId,
        }),
      });
      return created.exercise_id;
    },
  });

  // Grava uma série preparatória (aquecimento genérico/da ficha ou série de
  // ajuste) com `set_type` `warmup`/`feeler`. Não dispara avaliação de IA
  // por série nem resumo de exercício. `set_number` é o próximo número
  // FÍSICO do exercício (unique `(exercise_id, set_number)`), independente
  // da numeração "Série N de S" das séries de trabalho. Aguarda o refetch da
  // activity antes de concluir, para os contadores derivados do backend já
  // incluírem a série quando `onSuccess` avançar o estado local.
  // `client_key` por série física (exercício + `set_number`): a nova tentativa
  // depois de um erro reusa a chave, e o servidor devolve a série já gravada
  // (`duplicate: true`) em vez de gravar outra. Sai do mapa no sucesso.
  const setClientKeysRef = useRef(new Map<string, string>());
  function setClientKeyFor(exerciseId: number, setNumber: number): string {
    const slot = `${session.session_id}#${exerciseId}#${setNumber}`;
    let key = setClientKeysRef.current.get(slot);
    if (!key) {
      key = newIdempotencyKey();
      setClientKeysRef.current.set(slot, key);
    }
    return key;
  }
  function releaseSetClientKey(exerciseId: number, setNumber: number) {
    setClientKeysRef.current.delete(`${session.session_id}#${exerciseId}#${setNumber}`);
  }

  // Trava síncrona contra toque duplo também nas séries preparatórias (mesma
  // proteção do "Finalizar série", ver `saveSetLatchRef`).
  const savePreparatoryLatchRef = useRef(createSubmitLatch());
  const savePreparatorySetMutation = useMutation({
    mutationFn: async (input: PreparatorySetInput) => {
      if (!current) throw new ApiError('Nenhum exercício atual.', 0);
      const exerciseId = await ensureExerciseMutation.mutateAsync();
      const physicalSetNumber = nextPhysicalSetNumber(current.loggedRow?.sets);
      await apiFetch(`/workout-sessions/${session.session_id}/exercises/${exerciseId}/sets`, {
        method: 'POST',
        body: JSON.stringify({
          set_number: physicalSetNumber,
          client_key: setClientKeyFor(exerciseId, physicalSetNumber),
          set_type: input.set_type,
          reps: input.reps,
          weight_kg: input.weight_kg,
          rpe: input.rpe ?? null,
          muscle_pain: input.muscle_pain ?? null,
          joint_pain: input.joint_pain ?? null,
          notes: input.notes ?? null,
        }),
      });
      releaseSetClientKey(exerciseId, physicalSetNumber);
      await queryClient.invalidateQueries({ queryKey: activityQueryKey });
    },
    onSettled: () => savePreparatoryLatchRef.current.release(),
  });

  // M100: "Concluir" do exercício laboral grava as N séries prescritas
  // (`planExercise.laboral`, pronto do backend) com `set_type` `laboral`, sem
  // RPE, descanso nem avaliação. O item sai da fila quando o refetch da
  // activity trouxer as séries (`isLaboralItemDone`).
  const saveLaboralLatchRef = useRef(createSubmitLatch());
  const saveLaboralMutation = useMutation({
    mutationFn: async () => {
      if (!current) throw new ApiError('Nenhum exercício atual.', 0);
      const prescription = laboralPrescriptionOf(current.planExercise);
      if (!prescription) throw new ApiError('Exercício laboral sem padrão.', 0);
      const exerciseId = await ensureExerciseMutation.mutateAsync();
      const records = laboralSetRecords(prescription, nextPhysicalSetNumber(current.loggedRow?.sets));
      for (const record of records) {
        await apiFetch(`/workout-sessions/${session.session_id}/exercises/${exerciseId}/sets`, {
          method: 'POST',
          body: JSON.stringify({
            ...record,
            client_key: setClientKeyFor(exerciseId, record.set_number),
            rpe: null,
          }),
        });
      }
      for (const record of records) releaseSetClientKey(exerciseId, record.set_number);
      await queryClient.invalidateQueries({ queryKey: activityQueryKey });
    },
    onSettled: () => saveLaboralLatchRef.current.release(),
  });

  function completeLaboral() {
    if (saveLaboralMutation.isPending || !saveLaboralLatchRef.current.tryEnter()) return;
    saveLaboralMutation.mutate();
  }

  // `setNumber`/`previousSets`/`isLastSet` são SEMPRE derivados de `current`
  // (calculado a partir de `activityQuery`, a fonte de verdade do servidor) —
  // nunca de um contador local, para não haver contagem duplicada quando o
  // refetch e o estado local ficam dessincronizados. O "próximo exercício"
  // NÃO é capturado aqui: é lido ao vivo de `nextPendingItem`, para refletir
  // um skip feito durante o próprio descanso.
  // Trava síncrona contra toque duplo no "Finalizar série"/"Concluir": o
  // `isPending` só muda no próximo render, então dois toques no mesmo quadro
  // gravariam a série duas vezes. Mesma proteção que as séries preparatórias
  // já têm (`savePreparatorySetMutation.isPending`), agora também aqui. Liberada
  // no `onSettled` do PRÓPRIO hook (sempre dispara, sucesso ou erro).
  const saveSetLatchRef = useRef(createSubmitLatch());
  const saveSetMutation = useMutation({
    mutationFn: async (values: SetLoggerValues) => {
      if (!current) throw new ApiError('Nenhum exercício atual.', 0);
      const tappedAt = Date.now();
      const exerciseId = await ensureExerciseMutation.mutateAsync();
      // Número exibido/avaliado ("Série N de S"): só trabalho + aplicações
      // de técnica. O `set_number` gravado é o FÍSICO (inclui
      // aquecimento/ajuste já gravados), ver `nextPhysicalSetNumber`.
      const setNumber = current.loggedCount + 1;
      const physicalSetNumber = nextPhysicalSetNumber(current.loggedRow?.sets);
      // Em advancedMode, "última série" = o exercício fica concluído com esta
      // gravação, contando também as aplicações da técnica já PULADAS (pular
      // uma do meio e gravar a última também é fim de exercício). Fora de
      // advancedMode, comportamento inalterado (`current.targetCount`).
      // Fase 5: a mesma regra nos dois modos — com rest-pause/drop-set, a
      // última série de TRABALHO não encerra o exercício (vêm as aplicações).
      const isLastSetOfExercise = isAdvancedExerciseDone({
        ...current.advancedCounters,
        loggedNonPreparatory: setNumber,
      });
      const evalKey = currentKey;
      // Modo Intensivo: a última série de trabalho de um item com drop set /
      // rest-pause abre as etapas da técnica SEM o descanso do item (drop set
      // emenda; rest-pause começa pela pausa curta da prescrição).
      const applicationProfile = applicationProfileFor(current.planExercise, current.targetCount);
      const opensApplication =
        applicationProfile != null &&
        !isLastSetOfExercise &&
        values.anchor_set_number == null &&
        isWorkingSetType(values.set_type) &&
        current.advancedCounters.techniqueTarget > 0 &&
        current.loggedWorkingCount + 1 >= current.targetCount;
      const applicationPauseSeconds = opensApplication && applicationProfile ? intensiveRestSeconds(applicationProfile) : 0;
      // Contexto de fadiga para a IA: aquecimento/ajuste ficam de fora.
      const previousSets = (current.loggedRow?.sets || []).filter((set) => !isPreparatorySetType(set.set_type));

      const saved = await apiFetch<SavedSetIntensity>(`/workout-sessions/${session.session_id}/exercises/${exerciseId}/sets`, {
        method: 'POST',
        body: JSON.stringify({
          set_number: physicalSetNumber,
          client_key: setClientKeyFor(exerciseId, physicalSetNumber),
          ...(values.set_type ? { set_type: values.set_type } : {}),
          reps: values.reps,
          weight_kg: values.weight_kg,
          rpe: values.rpe,
          // Lote T5: `{}` em série de repetições (payload igual ao de antes).
          ...setMeasurePayload(values),
          // M102: `{}` sem lados; com lados o servidor grava o pior lado.
          ...setSidesPayload(values),
          rest_seconds:
            advancedStage?.kind === 'technique'
              ? (planTechniquePauseSeconds(current.planExercise) ?? current.planExercise.rest_seconds ?? null)
              : (current.planExercise.rest_seconds ?? null),
          muscle_pain: values.muscle_pain,
          joint_pain: values.joint_pain,
          notes: values.notes,
          ...(values.technique_code ? { technique_code: values.technique_code } : {}),
          ...(values.anchor_set_number != null ? { anchor_set_number: values.anchor_set_number } : {}),
        }),
      });

      // Família além da falha: as reps extras lançadas no card da última série
      // de trabalho viram UMA linha `forced_rep` ligada a ela (a série recém-
      // gravada é a âncora).
      const extraReps = values.extra_reps ?? 0;
      if (extraReps > 0 && current.planExercise.advanced_technique) {
        await apiFetch(`/workout-sessions/${session.session_id}/exercises/${exerciseId}/sets`, {
          method: 'POST',
          body: JSON.stringify({
            set_number: physicalSetNumber + 1,
            client_key: setClientKeyFor(exerciseId, physicalSetNumber + 1),
            set_type: getAdvancedTechniqueSetType(current.planExercise.advanced_technique),
            reps: extraReps,
            weight_kg: values.weight_kg,
            rpe: null,
            technique_code: current.planExercise.advanced_technique,
            anchor_set_number: physicalSetNumber,
          }),
        });
        releaseSetClientKey(exerciseId, physicalSetNumber + 1);
      }
      releaseSetClientKey(exerciseId, physicalSetNumber);

      // Aguarda o refetch antes de considerar a série salva: garante que
      // `current.loggedCount` já reflita a série recém-gravada quando a
      // próxima tela renderizar.
      await queryClient.invalidateQueries({ queryKey: activityQueryKey });
      return {
        exerciseId,
        setNumber,
        // M102: com lados, reps/RPE oficiais vêm da resposta do servidor.
        values: withSavedCanonical(values, saved),
        previousSets,
        isLastSetOfExercise,
        evalKey,
        saved,
        opensApplication,
        applicationPauseSeconds,
        tappedAt,
      };
    },
    onSettled: () => saveSetLatchRef.current.release(),
    onSuccess: ({
      exerciseId,
      setNumber,
      values,
      previousSets,
      isLastSetOfExercise,
      evalKey,
      saved,
      opensApplication,
      applicationPauseSeconds,
      tappedAt,
    }) => {
      // Só aqui (sucesso confirmado pelo servidor) o descanso passa a dizer
      // "SÉRIE SALVA": erro nunca chega a este ponto.
      setLastSavedSet({ forKey: evalKey, saved: saved ?? {} });
      if (opensApplication) {
        setApplicationAdjust(null);
        setTechniquePause(
          applicationPauseSeconds > 0 && evalKey
            ? { key: evalKey, endsAt: tappedAt + applicationPauseSeconds * 1000, totalSeconds: applicationPauseSeconds }
            : null,
        );
      } else {
        beginRest({
          key: `${evalKey}#s${setNumber}`,
          isExerciseTransition: isLastSetOfExercise,
          afterStageKind: null,
          fromKey: isLastSetOfExercise ? evalKey : null,
          setSaved: true,
        });
      }
      if (perSetAiEnabled) {
        runEvaluation(exerciseId, setNumber, values, previousSets, evalKey);
      } else {
        setEvaluation(IDLE_EVALUATION);
      }
      if (isLastSetOfExercise) {
        runExerciseSummary(exerciseId);
      } else {
        setExerciseSummary(IDLE_SUMMARY);
      }
    },
  });

  function submitSet(values: SetLoggerValues) {
    if (saveSetMutation.isPending || !saveSetLatchRef.current.tryEnter()) return;
    saveSetMutation.mutate(values);
  }

  // Modo Intensivo — etapa da técnica (drop set / rest-pause). Mesmo vínculo
  // da Fase 5 (`technique_code` + `anchor_set_number`), carga e origem da
  // etapa (`intensiveApplicationPayload`), sem RPE individual. Entre etapas não
  // há descanso do item: drop set emenda; rest-pause abre a pausa da
  // prescrição (contada da hora do toque). A última etapa abre a transição.
  type ApplicationSaveInput = {
    reps: number | null;
    planned: IntensivePlannedLoad;
    techniqueCode: string;
    anchorSetNumber: number | null;
    pauseSeconds: number;
  };
  const saveApplicationLatchRef = useRef(createSubmitLatch());
  const saveApplicationMutation = useMutation({
    mutationFn: async (input: ApplicationSaveInput) => {
      if (!current) throw new ApiError('Nenhum exercício atual.', 0);
      const tappedAt = Date.now();
      const exerciseId = await ensureExerciseMutation.mutateAsync();
      const setNumber = current.loggedCount + 1;
      const physicalSetNumber = nextPhysicalSetNumber(current.loggedRow?.sets);
      const isLastSetOfExercise = isAdvancedExerciseDone({
        ...current.advancedCounters,
        loggedNonPreparatory: setNumber,
      });
      const evalKey = currentKey;
      await apiFetch(`/workout-sessions/${session.session_id}/exercises/${exerciseId}/sets`, {
        method: 'POST',
        body: JSON.stringify(
          intensiveApplicationPayload({
            setNumber: physicalSetNumber,
            clientKey: setClientKeyFor(exerciseId, physicalSetNumber),
            setType: getAdvancedTechniqueSetType(input.techniqueCode),
            techniqueCode: input.techniqueCode,
            anchorSetNumber: input.anchorSetNumber,
            reps: input.reps,
            planned: input.planned,
            completedAt: new Date(tappedAt).toISOString(),
            restSeconds: input.pauseSeconds,
          }),
        ),
      });
      releaseSetClientKey(exerciseId, physicalSetNumber);
      await queryClient.invalidateQueries({ queryKey: activityQueryKey });
      return { exerciseId, setNumber, isLastSetOfExercise, evalKey, tappedAt, pauseSeconds: input.pauseSeconds };
    },
    onSettled: () => saveApplicationLatchRef.current.release(),
    onSuccess: ({ exerciseId, setNumber, isLastSetOfExercise, evalKey, tappedAt, pauseSeconds }) => {
      setApplicationAdjust(null);
      if (isLastSetOfExercise) {
        setTechniquePause(null);
        setLastSavedSet(null);
        runExerciseSummary(exerciseId);
        beginRest({
          key: `${evalKey}#s${setNumber}`,
          isExerciseTransition: true,
          afterStageKind: null,
          fromKey: evalKey,
          setSaved: true,
        });
        return;
      }
      setTechniquePause(
        pauseSeconds > 0 && evalKey
          ? { key: evalKey, endsAt: tappedAt + pauseSeconds * 1000, totalSeconds: pauseSeconds }
          : null,
      );
    },
  });

  function submitApplication(input: ApplicationSaveInput) {
    if (saveApplicationMutation.isPending || !saveApplicationLatchRef.current.tryEnter()) return;
    saveApplicationMutation.mutate(input);
  }

  // PATCH de uma série gravada (confirmação de reps pendentes / correção).
  // 409 = sessão já finalizada; 404 = série sumiu: viram mensagem legível.
  async function patchSessionSet(exerciseId: number, setNumber: number, body: Record<string, number>) {
    try {
      await apiFetch(`/workout-sessions/${session.session_id}/exercises/${exerciseId}/sets/${setNumber}`, {
        method: 'PATCH',
        body: JSON.stringify(body),
      });
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        throw new Error('O treino já foi finalizado: corrija a série pelo histórico.');
      }
      if (error instanceof ApiError && error.status === 404) {
        throw new Error('Série não encontrada no servidor. Atualize a tela e tente de novo.');
      }
      throw new Error(errorMessage(error, 'Não foi possível corrigir a série.'));
    }
  }

  // Avaliação de IA por série — só quando o toggle "IA por série" está ligado
  // (`perSetAiEnabled`). Roda em paralelo ao `RestTimer`; erro vira
  // "avaliação indisponível" e nunca trava o treino.
  function runEvaluation(
    exerciseId: number,
    setNumber: number,
    values: SetLoggerValues,
    previousSets: ActivityDetail['exercises'][number]['sets'],
    evalKey: string | null,
  ) {
    if (!current) return;
    setEvaluation({ loading: true, error: false, result: null, forKey: evalKey });
    fetchWorkoutAiResult<SetEvaluationResult>('/exercises/evaluate-set', {
      method: 'POST',
      body: JSON.stringify({
        exercise_name: effectiveExerciseName,
        // R1 (regras de carga): sessão + `exercises.id` para o servidor ler a
        // faixa de fadiga prévia do exercício (o mesmo leitor do salvamento).
        session_id: session.session_id,
        exercise_id: exerciseId,
        reps_target: effectiveRepsTarget || null,
        load_target: effectiveLoadTarget || null,
        rpe_target: effectiveRpeTarget || null,
        reps: values.reps,
        weight_kg: values.weight_kg,
        rpe: values.rpe,
        // Lote T5: a IA recebe a medida (neutra fora de reps, `SetEvaluationIn.measure`).
        ...setMeasurePayload(values),
        // M102: lados da série unilateral (só texto do prompt).
        ...setSidesPayload(values),
        set_number: setNumber,
        sets_count: current.targetCount,
        muscle_pain: values.muscle_pain,
        joint_pain: values.joint_pain,
        notes: values.notes,
        // Par exercício × equipamento + teto da prescrição: o SERVIDOR clampa a
        // sugestão da IA antes de responder (PR-14, furo G6). Sem isso a
        // sugestão chegava aqui sem limite nenhum e era aplicada direto como
        // default da próxima série.
        mapping_id: effectiveMappingId,
        equipment_id: effectiveEquipmentLinkId,
        effective_rpe_ceiling: effectiveRpeCeiling,
        // Lote 6/P9: só série de trabalho tem status do motor para a IA explicar.
        set_type: values.set_type ?? null,
        // `set_number` ordinal (mesma numeração de "Série N de S"), não o
        // físico; `set_type` marca aplicações de técnica para a IA.
        previous_sets_this_session: previousSets.map((set, index) => ({
          set_number: index + 1,
          set_type: set.set_type ?? null,
          reps: set.reps ?? null,
          weight_kg: set.weight_kg != null ? Number(set.weight_kg) : null,
          rpe: set.rpe != null ? Number(set.rpe) : null,
        })),
      }),
    })
      .then((result) => setEvaluation({ loading: false, error: false, result, forKey: evalKey }))
      .catch(() => setEvaluation({ loading: false, error: true, result: null, forKey: evalKey }));
  }

  // Resumo de fim de EXERCÍCIO — incondicional (independe de
  // `perSetAiEnabled`), disparado quando a última série do exercício é salva.
  function runExerciseSummary(exerciseId: number) {
    setExerciseSummary({ loading: true, error: false, result: null });
    fetchWorkoutAiResult<ExerciseSummaryResult>(`/exercises/${exerciseId}/summarize`, { method: 'POST' })
      .then((result) => setExerciseSummary({ loading: false, error: false, result }))
      .catch(() => setExerciseSummary({ loading: false, error: true, result: null }));
  }

  // Ao fim da ÚLTIMA série de um exercício, zerar o cronômetro NÃO avança
  // sozinho: o usuário confirma em "Continuar" no `NextExercisePreviewCard`.
  // Pular o descanso (`skipped`) ou o fim da ficha avançam direto.
  function handleRestFinished(skipped: boolean) {
    if (restIsExerciseTransition && current && !skipped) return;
    endRest();
    // `evaluation` NÃO é limpo aqui: a sugestão numérica precisa sobreviver
    // até o `SetLoggerCard` da próxima série usá-la como default.
    setExerciseSummary(IDLE_SUMMARY);
  }

  function openSkipModalFor(workoutPlanExerciseId: number | null | undefined) {
    if (workoutPlanExerciseId == null) return;
    setSkipTargetId(workoutPlanExerciseId);
    openSkipModal();
  }

  function handleSkipSelect(chosenWorkoutPlanExerciseId: number) {
    if (skipTargetId == null) return;
    const nextSkipped = new Set(skippedIds);
    nextSkipped.add(skipTargetId);
    setSkippedIds(nextSkipped);
    setPriorityId(chosenWorkoutPlanExerciseId);
    persistSession({
      skipped_exercise_ids: Array.from(nextSkipped),
      jump_target_exercise_id: chosenWorkoutPlanExerciseId,
    });
    closeSkipModal();
    setSkipTargetId(null);
    handleRestFinished(true);
  }

  function handleCancel(workoutPlanExerciseId: number | null | undefined) {
    if (workoutPlanExerciseId == null) return;
    const nextCancelled = new Set(cancelledIds);
    nextCancelled.add(workoutPlanExerciseId);
    setCancelledIds(nextCancelled);
    persistSession({ cancelled_exercise_ids: Array.from(nextCancelled) });
    handleRestFinished(true);
  }

  // ── Substituição de equipamento ─────────────────────────────────────────
  async function handleSubstituteSelect(choice: EquipmentSubstituteChoice) {
    if (!current) return;

    // Escolher um exercício que a própria ficha já prescreve em OUTRO item
    // pendente não é substituição: é pular para aquele item (ver
    // `findPlanItemForSubstitutedExercise`). Sem isso a execução era gravada
    // no slot do item ATUAL — que nunca era exibido de novo — e o item
    // prescrito continuava pendente, voltando ao fim da fila para ser
    // executado uma segunda vez (atividade 230, ficha 119, 2026-09-25).
    const jumpTargetId = findPlanItemForSubstitutedExercise({
      planExercises: orderedExercises,
      chosenMappingId: choice.mapping_id,
      currentWorkoutPlanExerciseId,
      isPending: (id) => progress.some((item) => !item.done && item.planExercise.workout_plan_exercise_id === id),
    });
    if (jumpTargetId != null && currentWorkoutPlanExerciseId != null) {
      // Com séries já gravadas, "trocar pelas séries restantes" ENCERRA o item
      // atual (as séries ficam no histórico), como já fazia antes de criar a
      // continuação sintética. Sem nada gravado o item vai para o FIM da fila,
      // exatamente como "Pular este exercício": continua pendente e volta a
      // ser exibido — nenhum item da ficha é consumido sem aparecer.
      const endsCurrent = Boolean(swapRemainingMode && current.loggedRow);
      const nextCancelled = new Set(cancelledIds);
      const nextSkipped = new Set(skippedIds);
      if (endsCurrent) nextCancelled.add(currentWorkoutPlanExerciseId);
      else nextSkipped.add(currentWorkoutPlanExerciseId);
      if (endsCurrent) setCancelledIds(nextCancelled);
      else setSkippedIds(nextSkipped);
      setPriorityId(jumpTargetId);
      persistSession({
        jump_target_exercise_id: jumpTargetId,
        cancelled_exercise_ids: Array.from(nextCancelled),
        skipped_exercise_ids: Array.from(nextSkipped),
      });
      setSwapRemainingMode(false);
      closeSubstitute();
      notifications.show({
        color: 'blue',
        message: endsCurrent
          ? `${choice.exercise_name} já está na sua ficha — pulei direto para ele.`
          : `${choice.exercise_name} já está na sua ficha — pulei direto para ele. ${current.planExercise.exercise_name} volta no fim da ficha.`,
      });
      return;
    }

    // Sem nenhum exercício gravado ainda (inclusive durante aquecimento/
    // ajuste), "trocar pelas séries restantes" equivale a trocar antes da 1ª
    // série: override no próprio item, que preserva a configuração avançada.
    const swapAsOverride = shouldSwapAsOverride({
      hasLoggedRow: Boolean(current.loggedRow),
      workoutPlanExerciseId: currentWorkoutPlanExerciseId,
    });

    if (swapRemainingMode && !swapAsOverride) {
      // "Trocar de equipamento (séries restantes)": encerra o slot original
      // (as séries já logadas continuam no histórico) e injeta uma
      // continuação sintética com o novo equipamento e alvo reduzido ao que
      // ainda falta. Alvos de reps/carga/RPE recalculados para o mapping_id
      // novo; em caso de falha, mantém os alvos antigos em vez de travar.
      setSwappingEquipment(true);
      let targets: Parameters<typeof buildSyntheticContinuation>[0]['targets'] = {
        reps_target: current.planExercise.reps_target ?? null,
        load_target: current.planExercise.load_target ?? null,
        rpe_target: current.planExercise.rpe_target ?? null,
      };
      try {
        // `equipment_id` escolhe a trilha do PAR NOVO (sem ele o backend usa a
        // trilha executada mais recentemente do exercício, que pode ser outra).
        // M97-D2: a sessão e o item trocado fazem a continuação sair no
        // objetivo do item e, numa sessão de deload, com a prescrição de deload.
        const suggestion = await apiFetch<SuggestedProgressionResponse>(
          pairSuggestionPath({
            mappingId: choice.mapping_id,
            equipmentLinkId: choice.equipment_link_id,
            sessionId: session.session_id,
            workoutPlanExerciseId: originalPlanItemId(currentWorkoutPlanExerciseId, midExerciseExtras),
            trainingGoal: current.planExercise.training_goal ?? null,
          }),
        );
        const suggested = suggestedProgressionTargets(suggestion);
        targets = {
          reps_target: suggested.reps_target,
          load_target: suggested.load_target,
          rpe_target: suggested.rpe_target,
          load_increment_kg: suggestion?.load_increment_kg ?? null,
          effective_rpe_ceiling: suggestion?.effective_rpe_ceiling ?? null,
          failure_allowed: suggestion?.failure_allowed ?? null,
        };
      } catch {
        // Fallback silencioso — mantém os alvos do exercício original.
      }
      setSwappingEquipment(false);

      // O piso de séries (`MIN_SETS_COUNT`) é aplicado por `buildSyntheticContinuation`.
      const remainingSets = current.targetCount - current.loggedCount;
      const localId = nextSyntheticIdRef.current;
      nextSyntheticIdRef.current -= 1;
      const originalId = current.planExercise.workout_plan_exercise_id ?? null;

      // Herda aquecimento/ajuste/técnica do item original: o ajuste volta a
      // ser oferecido com o equipamento novo (nunca pulado em silêncio); o
      // aquecimento já feito/pulado do grupo muscular continua valendo.
      const extra: MidExerciseExtra = buildSyntheticContinuation({
        localId,
        current: current.planExercise,
        remainingSets,
        choice,
        targets,
        existingExtras: midExerciseExtras,
      });

      const nextExtras = [...midExerciseExtras, extra];
      const nextCancelled = new Set(cancelledIds);
      if (originalId != null) nextCancelled.add(originalId);
      const nextAdvancedProgress = {
        ...advancedProgress,
        [localId]: progressAfterEquipmentSwap({ warmupDone: advancedWarmupDoneCount }),
      };
      const nextWarmup = { ...warmup, [localId]: { ...currentWarmupState } };

      setMidExerciseExtras(nextExtras);
      setCancelledIds(nextCancelled);
      setPriorityId(localId);
      setAdvancedProgress(nextAdvancedProgress);
      setWarmup(nextWarmup);
      persistSession({
        mid_exercise_extras: nextExtras,
        cancelled_exercise_ids: Array.from(nextCancelled),
        jump_target_exercise_id: localId,
        advanced_stage_progress: nextAdvancedProgress,
        warmup_state: nextWarmup,
      });

      setSwapRemainingMode(false);
      closeSubstitute();
      return;
    }

    // Troca antes da 1ª série: só um override no item da ficha. A meta de
    // séries é recalculada por `overrideSuggestionQueries` (acima), que já
    // reage ao novo mapping_id.
    if (currentWorkoutPlanExerciseId != null) {
      const nextOverrides = {
        ...overrides,
        [currentWorkoutPlanExerciseId]: {
          mapping_id: choice.mapping_id,
          equipment_link_id: choice.equipment_link_id,
          exercise_name: choice.exercise_name,
          equipment_name: choice.equipment_name,
        } satisfies Override,
      };
      // Série de ajuste é calibração do EQUIPAMENTO: com o novo, volta a ser
      // oferecida mesmo que tenha sido pulada no anterior.
      const nextAdvancedProgress = {
        ...advancedProgress,
        [currentWorkoutPlanExerciseId]: progressAfterEquipmentSwap({ warmupDone: advancedWarmupDoneCount }),
      };
      setOverrides(nextOverrides);
      setAdvancedProgress(nextAdvancedProgress);
      persistSession({ equipment_overrides: nextOverrides, advanced_stage_progress: nextAdvancedProgress });
    }
    setSwapRemainingMode(false);
    closeSubstitute();
  }

  const finishMutation = useMutation({
    mutationFn: () => finishWorkoutSession(session.session_id),
    onSuccess: () => onFinish(session.session_id),
  });

  // "Finalizar treino": séries com reps pendentes são confirmadas ANTES (depois
  // do fim o PATCH devolve 409). "Finalizar mesmo assim" segue com elas.
  const pendingRepsSets = collectPendingRepsSets(activity?.exercises);
  function requestFinish() {
    if (finishNeedsPendingConfirmation(pendingRepsSets)) {
      setPendingFinishOpen(true);
      return;
    }
    finishMutation.mutate();
  }
  async function confirmPendingReps(item: PendingRepsSet, reps: number) {
    await patchSessionSet(item.exerciseId, item.setNumber, { reps });
    await queryClient.invalidateQueries({ queryKey: activityQueryKey });
  }
  const pendingFinishModal = (
    <PendingRepsFinishModal
      opened={pendingFinishOpen}
      items={pendingRepsSets}
      weightUnit={weightUnit}
      finishing={finishMutation.isPending}
      error={finishMutation.isError ? errorMessage(finishMutation.error, 'Não foi possível finalizar o treino.') : null}
      onConfirm={confirmPendingReps}
      onFinish={() => finishMutation.mutate()}
      onCancel={() => setPendingFinishOpen(false)}
    />
  );

  // ── Render ──────────────────────────────────────────────────────────────
  if (activityQuery.isLoading || planQuery.isLoading) {
    return (
      <Group justify="center" py="xl">
        <Loader size="md" color="brand" />
      </Group>
    );
  }

  if (activityQuery.isError || !activity) {
    return (
      <Stack gap="sm" py="md">
        <Alert color="red" variant="light" icon={<IconAlertTriangle size={16} />}>
          <Text size="sm">Não foi possível carregar a sessão de treino. Verifique sua conexão e tente novamente.</Text>
        </Alert>
        <Button
          radius="xl"
          loading={activityQuery.isFetching}
          onClick={() => {
            activityQuery.refetch();
            planQuery.refetch();
          }}
        >
          Tentar novamente
        </Button>
      </Stack>
    );
  }

  if (extendMode) {
    return (
      <FreeformScreen
        sessionId={session.session_id}
        gymId={session.gym_id}
        perSetAiEnabled={perSetAiEnabled}
        title="Adicionar exercício avulso"
        onFinish={onFinish}
        onReturnToPlan={() => setExtendMode(false)}
      />
    );
  }

  if (!current) {
    return (
      <Stack gap="md" py="xl" align="center">
        <IconCheck size={48} color="var(--mantine-color-green-6)" />
        <Title order={2} c={BRAND.textPrimary}>
          Ficha concluída!
        </Title>
        <Text c={BRAND.textMuted} ta="center">
          Todos os {orderedExercises.length} exercícios desta ficha foram registrados.
        </Text>
        <Button
          size="lg"
          radius="xl"
          h={56}
          fullWidth
          color="green"
          leftSection={<IconPlayerStop size={18} />}
          loading={finishMutation.isPending}
          onClick={requestFinish}
        >
          Encerrar treino
        </Button>
        <Button size="lg" radius="xl" h={56} fullWidth variant="light" onClick={() => setExtendMode(true)}>
          Adicionar exercício avulso
        </Button>
        {finishMutation.isError && (
          <Alert color="red" variant="light" icon={<IconAlertTriangle size={16} />}>
            {errorMessage(finishMutation.error, 'Não foi possível finalizar o treino.')}
          </Alert>
        )}
        {pendingFinishModal}
      </Stack>
    );
  }

  const setNumber = current.loggedCount + 1;
  // "Série N de S": só séries de trabalho (as aplicações de técnica são
  // subitens da âncora).
  const workingSetNumber = current.loggedWorkingCount + 1;
  // Referência de carga/reps/RPE para a próxima série de trabalho: a última
  // série de TRABALHO (âncora) — nunca aquecimento/ajuste (a carga de 20%
  // viraria o default) nem aplicação de técnica (o último drop, mais leve,
  // viraria a carga da série seguinte). Sem série `working` gravada (dado
  // antigo), cai na última não preparatória.
  const loggedSetsSoFar = (current.loggedRow?.sets || []).filter((set) => !isPreparatorySetType(set.set_type));
  const lastSet = findAnchorWorkingSet(loggedSetsSoFar) ?? loggedSetsSoFar[loggedSetsSoFar.length - 1];

  const suggestion = evaluation.result && evaluation.forKey === currentKey ? evaluation.result : null;
  // Campos e valores iniciais pela medida (núcleo, Lote T5). Em reps: IA >
  // última série > alvo da ficha > fallback do backend, como sempre.
  const measureDefaults = measureLoggerDefaults({
    measure: measureSpec.measure,
    durationSecondsTarget: measureSpec.durationSecondsTarget,
    timeWindowSeconds: measureSpec.timeWindowSeconds,
    repsTarget: effectiveRepsTarget,
    lastSet: lastSet ?? null,
    suggestedReps: suggestion?.suggested_next_reps ?? null,
  });
  const defaultReps = measureDefaults.reps;
  // Carga da próxima série: sugestão da IA (cuja carga final já é a da regra
  // do motor quando ela se aplica) > carga da próxima série devolvida pelo
  // MOTOR no salvamento da série (`next_set_load`, regra de 2026-10-12, vale
  // também sem IA) > carga da última série de trabalho > alvo da prescrição.
  const engineNextLoad =
    lastSavedSet && lastSavedSet.forKey === currentKey ? (lastSavedSet.saved.next_set_load?.load_kg ?? null) : null;
  // Fora de reps a carga é opcional e sem sugestão: a da última série, se houver.
  const repsDefaultWeight =
    suggestion?.suggested_next_load_kg != null
      ? suggestion.suggested_next_load_kg
      : engineNextLoad != null
        ? engineNextLoad
        : lastSet?.weight_kg != null
          ? Number(lastSet.weight_kg)
          : (parseNumericTarget(effectiveLoadTarget) ?? SET_LOGGER_FALLBACK_LOAD_KG);
  const measuredDefaultWeight = lastSet?.weight_kg != null ? Number(lastSet.weight_kg) : null;
  const defaultWeight = measureDefaults.loadOptional ? measuredDefaultWeight : repsDefaultWeight;
  // Sugestão da IA (já clampada no servidor) > RPE da última série desta
  // sessão > alvo da prescrição. `clampRpeSuggestion` garante que nem o RPE
  // repetido da última série ofereça um valor acima do teto de hoje (§5.7: o
  // que foi registrado não é reescrito, mas a tela nunca OFERECE 10 onde a
  // falha não é permitida).
  const defaultRpe = clampRpeSuggestion(
    suggestion?.suggested_next_rpe != null
      ? suggestion.suggested_next_rpe
      : lastSet?.rpe != null
        ? Number(lastSet.rpe)
        : parseNumericTarget(effectiveRpeTarget),
    effectiveRpeCeiling,
  );

  // Alvo de RPE da série de TRABALHO do modo avançado: prescrição congelada
  // (com overlay) > sugestão da IA/última série (`defaultRpe`), sempre
  // limitado pelo teto de hoje. Nunca 10 num par sem falha permitida.
  const advancedWorkingDefaultRpe = prescribedEffectiveRpe ?? defaultRpe;

  // Evento de segurança JÁ GRAVADO pelo servidor em alguma série deste
  // exercício (`exercise_sets.safety_event`, §5.7): RPE 10 registrado com a
  // falha negada. Exibido ao usuário, sem bloquear nada. O alerta de segurança
  // (par que proíbe a falha) vence o informativo (só a recuperação negou).
  const loggedSafetyEvents = (current.loggedRow?.sets || []).map((set) => set.safety_event).filter(Boolean);
  const currentSafetyEvent =
    loggedSafetyEvents.find((event) => isSafetyAlertEvent(event)) ?? loggedSafetyEvents[0] ?? null;

  const originalSetsCount = current.planExercise.sets_count ?? null;
  const adjustedSetsCount =
    currentWorkoutPlanExerciseId != null ? overrideAdjustedSetsCount[currentWorkoutPlanExerciseId] : undefined;
  const showAdjustedSetsBadge =
    adjustedSetsCount != null && originalSetsCount != null && adjustedSetsCount !== originalSetsCount;

  // "Carga de trabalho alvo" do exercício — mesma referência de prioridade
  // já usada como `defaultWeight` das séries de TRABALHO normais (sugestão
  // da IA > última série de trabalho NESTA sessão > `load_target` da ficha,
  // fallback 20kg). É a âncora das cargas de aquecimento, ajuste e técnica:
  // quando bate com a âncora prescrita, as cargas vêm prontas da API; senão,
  // do espelho do backend (ver `workout-mode-load-suggestions.ts`).
  // Só reps tem aquecimento/ajuste/técnica: a âncora é sempre a carga de reps.
  const referenceWorkingLoad = repsDefaultWeight;

  // ── Fase 5: aplicação da técnica (mini-bloco/drop) ─────────────────────
  // Âncora = última série de trabalho gravada; a carga do drop parte DELA
  // (não da última linha gravada, que já pode ser um drop), e o RPE padrão é
  // o dela.
  const loggedRowSets = current.loggedRow?.sets || [];
  const techniqueAnchor = advancedStage?.kind === 'technique' ? findAnchorWorkingSet(loggedRowSets) : null;
  const techniqueAnchorSetNumber = techniqueAnchor?.set_number ?? null;
  const techniqueAnchorWeight =
    techniqueAnchor?.weight_kg != null && Number.isFinite(Number(techniqueAnchor.weight_kg))
      ? Number(techniqueAnchor.weight_kg)
      : referenceWorkingLoad;
  const techniqueAnchorRpe =
    techniqueAnchor?.rpe != null && Number.isFinite(Number(techniqueAnchor.rpe)) ? Number(techniqueAnchor.rpe) : null;
  const techniqueBlockRows = blockApplicationRows(loggedRowSets, techniqueAnchorSetNumber);
  const techniqueCatalog = techniqueCatalogFromItems(techniqueCatalogItems);
  const warmupStageDefaultWeight =
    advancedStage?.kind === 'warmup' ? warmupLoad(referenceWorkingLoad, sessionLoadContext) : null;
  const feelerStageDefaultWeight =
    advancedStage?.kind === 'feeler'
      ? feelerSetLoad(referenceWorkingLoad, advancedStage.index, sessionLoadContext)
      : null;
  const techniqueApplicationDefaultWeight =
    advancedStage?.kind === 'technique' && advancedTechniqueValue
      ? techniqueApplicationLoad(advancedTechniqueValue, techniqueAnchorWeight, advancedStage.index, sessionLoadContext)
      : null;
  const advancedStageDefaultWeight =
    advancedStage?.kind === 'warmup'
      ? (warmupStageDefaultWeight ?? defaultWeight)
      : advancedStage?.kind === 'feeler'
        ? (feelerStageDefaultWeight ?? defaultWeight)
        : advancedStage?.kind === 'technique'
          ? (techniqueApplicationDefaultWeight ?? defaultWeight)
          : defaultWeight;

  // M22 — carga da PRÓXIMA série, anunciada no descanso para o usuário já
  // montar o aparelho. NÃO recalcula nada: reusa EXATAMENTE os mesmos valores
  // que o `SetLoggerCard` receberá quando o descanso acabar
  // (`advancedStageDefaultWeight` já cai em `defaultWeight` fora do modo
  // avançado), e a mesma sub-etapa (`advancedStage`, que durante o descanso
  // já aponta para a etapa seguinte — é ela que define o tempo de descanso
  // logo abaixo). Divergir daqui seria mostrar um número no descanso e outro
  // no registro.
  const nextSetPreview = resolveNextSetPreview({
    isExerciseTransition: restIsExerciseTransition,
    stage: advancedStage,
    techniqueApplicationNoun: advancedTechniqueValue
      ? getAdvancedTechniqueApplicationNoun(advancedTechniqueValue)
      : null,
    workingSetNumber,
    workingSetTotal: current.targetCount,
    weightKg: advancedStageDefaultWeight,
    measure: measureSpec.measure,
  });

  // Variante do `SetLoggerCard` que o ramo atual desenha (null = nenhum card
  // de série: descanso, escolha de equipamento, briefing ou aquecimento).
  const advancedStageVariant =
    advancedStage?.kind === 'warmup' ? 'advancedWarmup' : advancedStage?.kind === 'feeler' ? 'advancedFeeler' : 'default';
  const workingSetVariant = hasAdvancedFlow ? 'advancedWorking' : 'default';
  const showBriefingScreen = showBriefing && briefingPlanId != null && currentWorkoutPlanExerciseId != null;

  // ── Modo Intensivo (FST-7) ─────────────────────────────────────────────
  // Entra na etapa das séries de TRABALHO de um item com técnica que tem
  // perfil (depois do briefing do exercício e do aquecimento/ajuste). O
  // estado do bloco é o snapshot local reconciliado com o servidor a cada
  // render (séries confirmadas, pendentes, `completed_at`). Sem perfil, nada
  // muda no fluxo normal.
  // O bloco cobre séries inteiras (FST-7, GVT) e a família além da falha;
  // drop set/rest-pause usam as etapas logo abaixo.
  const currentIntensiveProfile =
    currentWorkoutPlanExerciseId != null ? intensiveProfileFor(current.planExercise, current.targetCount) : null;
  const intensiveProfile = isIntensiveBlockProfile(currentIntensiveProfile) ? currentIntensiveProfile : null;

  // ── Modo Intensivo: etapas da técnica (drop set / rest-pause) ───────────
  // A sub-etapa e a contagem são as da Fase 5 (`advancedStage`); a carga do
  // protocolo é a mesma do card antigo (`techniqueApplicationLoad`, dono
  // `session_loads`). Sem perfil (técnica fora do recorte), o card antigo.
  const applicationProfile = currentIntensiveProfile?.stageKind === 'application' ? currentIntensiveProfile : null;
  const applicationStage =
    applicationProfile && advancedStage?.kind === 'technique' && advancedTechniqueValue ? advancedStage : null;
  const applicationNoun = advancedTechniqueValue ? getAdvancedTechniqueApplicationNoun(advancedTechniqueValue) : '';
  const applicationKey =
    applicationStage && currentKey ? intensiveApplicationKey(currentKey, techniqueAnchorSetNumber, applicationStage.index) : '';
  const lastApplicationRow = techniqueBlockRows.length > 0 ? techniqueBlockRows[techniqueBlockRows.length - 1] : null;
  const lastApplicationKg =
    lastApplicationRow?.weight_kg != null && Number.isFinite(Number(lastApplicationRow.weight_kg))
      ? Number(lastApplicationRow.weight_kg)
      : null;
  const applicationPlanned: IntensivePlannedLoad | null =
    applicationStage && applicationProfile
      ? intensiveApplicationPlannedLoad({
          profile: applicationProfile,
          protocolKg: techniqueApplicationDefaultWeight ?? techniqueAnchorWeight,
          previousApplicationKg: lastApplicationKg,
          adjustment: applicationAdjust,
          key: applicationKey,
        })
      : null;
  const applicationPause = applicationStage && techniquePause?.key === currentKey ? techniquePause : null;

  function handleApplicationComplete(reps: number | null) {
    if (!applicationStage || !applicationProfile || !applicationPlanned || !advancedTechniqueValue) return;
    submitApplication({
      reps,
      planned: applicationPlanned,
      techniqueCode: advancedTechniqueValue,
      anchorSetNumber: techniqueAnchorSetNumber,
      pauseSeconds: intensiveRestSeconds(applicationProfile),
    });
  }
  const intensiveServerSets = current.loggedRow?.sets || [];
  const intensiveBlock =
    intensiveProfile && currentWorkoutPlanExerciseId != null
      ? reconcileIntensiveBlock({
          stored: intensiveBlocks[currentWorkoutPlanExerciseId],
          profile: intensiveProfile,
          serverSets: intensiveServerSets,
          initialPlannedKg: referenceWorkingLoad,
          now: Date.now(),
        })
      : null;
  const showIntensive =
    intensiveBlock != null &&
    intensiveBlock.phase !== 'TECNICA_CONCLUIDA' &&
    !resting &&
    !needsEquipmentChoice &&
    !showBriefingScreen &&
    !showWarmup &&
    advancedStage == null;
  const intensiveLoadSuffix = intensiveLoadConventionSuffix(previousSessionSetsQuery.data?.item?.load_convention);

  // Evento na máquina do bloco do item ATUAL. A base é sempre o valor mais
  // novo (reconciliado no próprio updater), então respostas assíncronas do
  // POST não desfazem um ajuste feito no meio tempo.
  function makeIntensiveDispatch(
    wpeId: number,
    profile: IntensiveProfile,
    serverSets: IntensiveServerSetsInput,
    initialKg: number | null,
  ) {
    return (event: IntensiveEvent) => {
      setIntensiveBlocks((prev) => {
        const base = reconcileIntensiveBlock({
          stored: prev[wpeId],
          profile,
          serverSets,
          initialPlannedKg: initialKg,
          now: Date.now(),
        });
        const next = { ...prev, [wpeId]: intensiveReducer(base, event) };
        persistIntensiveBlocks(next);
        return next;
      });
    };
  }
  const intensiveDispatch =
    intensiveProfile && currentWorkoutPlanExerciseId != null
      ? makeIntensiveDispatch(currentWorkoutPlanExerciseId, intensiveProfile, intensiveServerSets, referenceWorkingLoad)
      : () => {};

  // Exercício da sessão para as séries do bloco: o já gravado ou UMA criação
  // compartilhada (duas séries concluídas antes da 1ª resposta não criam dois).
  function ensureIntensiveExerciseId(): Promise<number> {
    if (current?.loggedRow) return Promise.resolve(current.loggedRow.id);
    const key = currentKey ?? '';
    const pending = intensiveExerciseIdRef.current.get(key);
    if (pending) return pending;
    const created = ensureExerciseMutation.mutateAsync();
    intensiveExerciseIdRef.current.set(key, created);
    created.catch(() => intensiveExerciseIdRef.current.delete(key));
    return created;
  }

  // POST da série (com `client_key`): o descanso já começou; o resultado só
  // marca a série como gravada ou com erro visível ("Tentar de novo"). Além da
  // falha: as reps extras vão numa linha da técnica ligada à série (mesma
  // regra do fluxo normal), com a própria chave; a nova tentativa reenvia as
  // duas (idempotente).
  async function sendIntensiveRecord(
    record: IntensiveSetRecord,
    dispatch: (event: IntensiveEvent) => void,
    restSeconds: number,
    techniqueCode: string,
  ) {
    try {
      const exerciseId = await ensureIntensiveExerciseId();
      await apiFetch(`/workout-sessions/${session.session_id}/exercises/${exerciseId}/sets`, {
        method: 'POST',
        body: JSON.stringify(intensiveSetPayload(record, restSeconds)),
      });
      const extra = intensiveExtraRepsPayload(record, {
        techniqueCode,
        setType: getAdvancedTechniqueSetType(techniqueCode),
      });
      if (extra) {
        await apiFetch(`/workout-sessions/${session.session_id}/exercises/${exerciseId}/sets`, {
          method: 'POST',
          body: JSON.stringify(extra),
        });
      }
      dispatch({ type: 'SAVE_OK', clientKey: record.clientKey });
      void queryClient.invalidateQueries({ queryKey: activityQueryKey });
    } catch (error) {
      dispatch({ type: 'SAVE_FAILED', clientKey: record.clientKey, error: errorMessage(error, SAVE_SET_ERROR) });
    }
  }

  function handleIntensiveComplete(reps: number | null, rpe: number | null, extraReps: number | null = null) {
    if (!intensiveBlock || !intensiveProfile || currentWorkoutPlanExerciseId == null) return;
    // Trava síncrona por série (toque duplo no mesmo quadro).
    const guard = `${session.session_id}#${currentWorkoutPlanExerciseId}#${intensiveBlock.currentSet}#${intensiveBlock.records.length}`;
    if (intensiveConcludedRef.current.has(guard)) return;
    intensiveConcludedRef.current.add(guard);
    const restSeconds = intensiveRestSeconds(intensiveProfile);
    const clientKey = newIdempotencyKey();
    const event: IntensiveEvent = {
      type: 'COMPLETE_SET',
      reps,
      rpe,
      clientKey,
      setNumber: nextIntensiveSetNumber(intensiveBlock, intensiveServerSets),
      now: Date.now(),
      restSeconds,
      ...(extraReps != null && extraReps > 0 ? { extraReps, extraClientKey: newIdempotencyKey() } : {}),
    };
    const record = intensiveReducer(intensiveBlock, event).records.find((row) => row.clientKey === clientKey);
    intensiveDispatch(event);
    if (record) void sendIntensiveRecord(record, intensiveDispatch, restSeconds, intensiveProfile.techniqueCode);
  }

  function handleIntensiveRetry(clientKey: string) {
    const record = intensiveBlock?.records.find((row) => row.clientKey === clientKey);
    if (!record || !intensiveProfile || record.sync === 'saving') return;
    intensiveDispatch({ type: 'RETRY', clientKey });
    void sendIntensiveRecord(record, intensiveDispatch, intensiveRestSeconds(intensiveProfile), intensiveProfile.techniqueCode);
  }

  // Correção na revisão / confirmação de reps pendentes: PATCH da série
  // gravada. Série ainda não gravada (erro) é corrigida localmente e
  // reenviada com a mesma chave.
  async function handleIntensiveCorrect(record: IntensiveSetRecord, correction: IntensiveSetCorrection) {
    if (record.sync !== 'saved') {
      if (record.sync === 'saving' || !intensiveProfile) return;
      const updated: IntensiveSetRecord = {
        ...record,
        reps: correction.reps ?? record.reps,
        weightKg: correction.weightKg ?? record.weightKg,
      };
      intensiveDispatch({ type: 'UPDATE_RECORD', clientKey: record.clientKey, reps: updated.reps, weightKg: updated.weightKg });
      intensiveDispatch({ type: 'RETRY', clientKey: record.clientKey });
      void sendIntensiveRecord(updated, intensiveDispatch, intensiveRestSeconds(intensiveProfile), intensiveProfile.techniqueCode);
      return;
    }
    const body: Record<string, number> = {};
    if (correction.reps != null) body.reps = correction.reps;
    if (correction.weightKg != null) body.weight_kg = correction.weightKg;
    const exerciseId = await ensureIntensiveExerciseId();
    await patchSessionSet(exerciseId, record.setNumber, body);
    intensiveDispatch({ type: 'UPDATE_RECORD', clientKey: record.clientKey, reps: correction.reps, weightKg: correction.weightKg });
    void queryClient.invalidateQueries({ queryKey: activityQueryKey });
  }

  // "Concluir FST-7": o item fica concluído e abre a transição de exercício
  // de sempre (resumo + próximo exercício).
  function handleIntensiveFinish() {
    if (!intensiveBlock || current == null) return;
    const hadRecords = intensiveBlock.records.length > 0;
    intensiveDispatch({ type: 'FINISH' });
    if (!hadRecords) return;
    setEvaluation(IDLE_EVALUATION);
    setLastSavedSet(null);
    if (current.loggedRow) runExerciseSummary(current.loggedRow.id);
    beginRest({
      key: `${currentKey}#intensive-done`,
      isExerciseTransition: true,
      afterStageKind: null,
      fromKey: currentKey,
      setSaved: true,
    });
  }
  const loggerVariant = showIntensive
    ? 'intensive'
    : resting || needsEquipmentChoice || showBriefingScreen || showWarmup
      ? null
      : advancedStage?.kind === 'technique' && advancedTechniqueValue
        ? applicationStage
          ? 'intensiveApplication'
          : 'techniqueApplication'
        : advancedStage
          ? advancedStageVariant
          : workingSetVariant;
  // Mapa "Conecte-se com os seus músculos": UM só por tela, recolhido, numa
  // linha baixa ACIMA do rodapé de ação (PR4: o CTA é sempre o fim do fluxo).
  // Aparece no descanso (dizendo de qual exercício são os músculos) e nos
  // cards de série que já o exibiam (padrão e série de trabalho do avançado).
  const showMuscleMap = !resting && (loggerVariant === 'default' || loggerVariant === 'advancedWorking');

  // "Trocar exercício" no descanso ENTRE séries: o mesmo fluxo que a execução
  // já oferece — antes da 1ª série gravada, troca limpa (override); depois,
  // "Trocar de equipamento (séries restantes)" do `FinishExerciseModal`. Na
  // transição de exercício não há o que trocar no exercício encerrado; o
  // próximo tem Trocar no briefing dele.
  const restIconActions: WorkoutIconAction[] =
    resting && !restIsExerciseTransition
      ? [
          {
            key: 'swap',
            label: 'Trocar exercício',
            caption: 'Trocar',
            icon: <IconArrowsExchange size={20} />,
            testId: 'rest-swap-exercise',
            onClick: () => {
              setSwapRemainingMode(Boolean(current.loggedRow));
              openSubstitute();
            },
          },
        ]
      : [];

  // Andamento global numa linha, na barra fina do topo (portal); sem a vaga
  // (ex.: renderização isolada), no topo da própria tela.
  const progressLine = (
    <div style={{ paddingBottom: 6 }}>
      <WorkoutOverallProgress
        positionLabel={positionLabel}
        completedCount={completedCount}
        totalCount={orderedExercises.length}
        trailing={<WorkoutModeNotice advanced={advancedMode} compact />}
      />
    </div>
  );
  // Próximo exercício numa linha, logo acima do rodapé do card de série.
  const nextExerciseLine =
    !resting && !(showBriefingScreen && !needsEquipmentChoice) ? (
      <NextExerciseIndicator nextItem={nextPendingItem} />
    ) : null;

  // M97 F3: objetivo do item (o carimbado pelo servidor na linha da ficha).
  const currentGoalBadge = <TrainingGoalBadge value={current?.planExercise.training_goal} onlyNonDefault />;

  return (
    <Stack gap={8} pt={8} className={`${workoutModeStyles.root} ${workoutModeStyles.screen}`} style={WORKOUT_MODE_CSS_VARS}>
      {headerSlot ? createPortal(progressLine, headerSlot) : progressLine}

      {/* M97 F3: sessão de deload (período ou avulsa) e o toggle até a 1ª série. */}
      <DeloadSessionBanner sessionId={session.session_id} />

      {currentSafetyEvent && (
        <Alert
          color={safetyEventColor(currentSafetyEvent)}
          variant="light"
          icon={isSafetyAlertEvent(currentSafetyEvent) ? <IconAlertTriangle size={16} /> : <IconInfoCircle size={16} />}
          py={4}
        >
          <Text size="xs">
            {buildSafetyEventBannerText(
              currentSafetyEvent,
              pairChanged ? { effective_rpe_ceiling: effectiveRpeCeiling } : prescription,
              pairChanged ? null : current?.planExercise.safety_overlay,
            )}
          </Text>
        </Alert>
      )}

      {resting ? (
        <RestTimerHero
          restKey={restKey ?? `${currentWorkoutPlanExerciseId}-${current.loggedCount}`}
          getStored={() => restTimerRef.current}
          onEndsAtChange={handleRestEndsAtChange}
          isExerciseTransition={restIsExerciseTransition}
          hasNextExercise={nextPendingItem != null}
          transitionNote={restIsExerciseTransition ? 'Exercício concluído' : null}
          iconActions={restIconActions}
          notice={
            finishMutation.isError ? errorMessage(finishMutation.error, 'Não foi possível finalizar o treino.') : null
          }
          initialSeconds={resolveRestSeconds({
              nextStageKind: advancedStage?.kind ?? null,
              isExerciseTransition: restIsExerciseTransition,
              completedStageKind: restAfterStageKind,
              warmupRestSeconds: current.planExercise.warmup_rest_seconds,
              feelerRestSeconds: current.planExercise.feeler_set_rest_seconds,
              techniqueDurationSeconds: planTechniquePauseSeconds(current.planExercise),
              restSeconds: current.planExercise.rest_seconds,
              // M97 F3: linha sem descanso usa o default do objetivo dela (API).
              defaultRestSeconds: goalDefaultRestSeconds(trainingGoals, current.planExercise.training_goal, DEFAULT_REST_SECONDS),
            })}
          onFinish={handleRestFinished}
          nextSet={nextSetPreview}
          weightUnit={weightUnit}
        >
          <RestSavedSetPanel
            view={restSavedSetView({
              setSaved: restSetSaved,
              saved: lastSavedSet?.saved,
              isExerciseTransition: restIsExerciseTransition,
              weightUnit,
            })}
            // A carga da próxima série já está no relógio: aqui não se repete.
            loadShownElsewhere={nextSetPreview?.weightKg != null}
          />
          <RestAiEvaluationCard
            enabled={perSetAiEnabled}
            loading={evaluation.loading}
            error={evaluation.error}
            result={evaluation.result}
          />
          {restIsExerciseTransition && (
            <ExerciseSummaryCard
              loading={exerciseSummary.loading}
              error={exerciseSummary.error}
              result={exerciseSummary.result}
            />
          )}
          {nextPendingItem ? (
            <NextExercisePreviewCard
              planExercise={nextPendingItem.planExercise}
              equipmentName={nextPendingItem.planExercise.equipment_name}
              equipmentPhotoUrl={nextEquipmentPhotoUrl}
              // A fadiga lida é a do `current`; ela só vale para o card quando
              // o próximo É o `current` (transição de exercício).
              fatigue={exerciseKey(nextPendingItem.planExercise) === currentKey ? currentGroupFatigue : null}
              actions={
                restIsExerciseTransition
                  ? {
                      onSkip: () => openSkipModalFor(nextPendingItem.planExercise.workout_plan_exercise_id),
                      onCancel: () => handleCancel(nextPendingItem.planExercise.workout_plan_exercise_id),
                      onFinish: requestFinish,
                      finishing: finishMutation.isPending,
                    }
                  : null
              }
            />
          ) : (
            <LastExerciseNotice isExerciseTransition={restIsExerciseTransition} />
          )}
          {restIsExerciseTransition && nextPendingItem && <RemainingExercisesList rows={remainingExerciseRows} />}
          {/* Mapa muscular (recolhido) e "Adicionar exercício avulso" numa
              fileira de linhas baixas; o mapa aberto ocupa a largura toda. */}
          <div className={workoutModeStyles.pairRow}>
            <MuscleMapDisclosure
              items={buildMuscleMapItems(current.planExercise.muscle_primary, current.planExercise.muscle_secondary)}
              title="Músculos"
              subject={
                restIsExerciseTransition
                  ? `Próximo exercício: ${effectiveExerciseName}`
                  : `Exercício atual: ${effectiveExerciseName}`
              }
              openClassName={workoutModeStyles.pairOpen}
            />
            <button
              type="button"
              className={workoutModeStyles.linkRow}
              onClick={() => setExtendMode(true)}
              aria-label="Adicionar exercício avulso"
              title="Adicionar exercício avulso"
              data-testid="rest-add-extra-exercise"
            >
              <span className={workoutModeStyles.linkRowIcon} aria-hidden="true">
                <IconCirclePlus size={18} />
              </span>
              <span className={workoutModeStyles.linkRowText} aria-hidden="true">
                Exercício avulso
              </span>
            </button>
          </div>
        </RestTimerHero>
      ) : currentLaboral ? (
        <LaboralExerciseCard
          exerciseName={effectiveExerciseName}
          equipmentName={effectiveEquipmentName}
          equipmentPhotoUrl={currentEquipmentPhotoUrl}
          prescription={currentLaboral}
          weightUnit={weightUnit}
          saving={saveLaboralMutation.isPending || ensureExerciseMutation.isPending}
          error={saveLaboralMutation.isError ? errorMessage(saveLaboralMutation.error, SAVE_SET_ERROR) : null}
          nextExerciseLine={nextExerciseLine}
          onComplete={completeLaboral}
          onSkip={() => handleCancel(currentWorkoutPlanExerciseId)}
        />
      ) : showIntensive && intensiveBlock && intensiveProfile ? (
        <IntensiveBlock
          state={intensiveBlock}
          profile={intensiveProfile}
          exerciseName={effectiveExerciseName}
          equipmentName={effectiveEquipmentName}
          repsTarget={effectiveRepsTarget || null}
          weightUnit={weightUnit}
          loadIncrementKg={loadIncrementKg}
          loadSuffix={intensiveLoadSuffix}
          nextExerciseLine={nextExerciseLine}
          dispatch={intensiveDispatch}
          onCompleteSet={handleIntensiveComplete}
          onRetry={handleIntensiveRetry}
          onCorrect={handleIntensiveCorrect}
          onFinish={handleIntensiveFinish}
          onSwap={() => {
            setSwapRemainingMode(Boolean(current.loggedRow));
            openSubstitute();
          }}
          onSkipExercise={() => openSkipModalFor(currentWorkoutPlanExerciseId)}
        />
      ) : needsEquipmentChoice ? (
        // Exercício travado até o usuário escolher o equipamento: nenhum
        // registro de série é oferecido aqui (o botão aparece desabilitado,
        // para deixar claro POR QUE não dá para começar). A escolha usa o
        // mesmo `EquipmentSubstituteModal` da troca de equipamento — nenhum
        // endpoint novo: ela vira um override local da sessão, que já é
        // persistido em `persistSession`.
        <Card withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
          <Stack gap="sm">
            <Title order={3} c={BRAND.textPrimary}>
              {effectiveExerciseName}
            </Title>
            <Alert color="orange" variant="light" icon={<IconAlertTriangle size={16} />}>
              <Text size="sm" fw={700} mb={4}>
                Escolha o equipamento antes de iniciar este exercício.
              </Text>
              <Text size="xs" c={BRAND.textMuted}>
                A ficha não conseguiu definir um equipamento desta academia para ele, então a carga e as séries só
                podem ser registradas depois da sua escolha.
              </Text>
            </Alert>
            <Button size="lg" radius="xl" h={56} fullWidth onClick={openSubstitute}>
              Escolher equipamento
            </Button>
            <Button size="lg" radius="xl" h={56} fullWidth variant="light" disabled>
              Registrar série
            </Button>
            <Button
              variant="subtle"
              size="sm"
              onClick={() => openSkipModalFor(currentWorkoutPlanExerciseId)}
            >
              Pular este exercício
            </Button>
          </Stack>
        </Card>
      ) : showBriefing && briefingPlanId != null && currentWorkoutPlanExerciseId != null ? (
        <ExerciseBriefingScreen
          planId={briefingPlanId}
          workoutPlanExerciseId={currentWorkoutPlanExerciseId}
          sessionId={session.session_id}
          goalBadge={currentGoalBadge}
          hideTechnique={Boolean(current.planExercise.advanced_technique) && advancedTechniqueValue == null}
          exerciseName={effectiveExerciseName}
          // Só quando a troca mudou o exercício: senão o próprio briefing
          // traz o card do item (mesmo exercício canônico).
          exerciseCard={exerciseCardSource?.kind === 'fetch' ? effectiveExerciseCard : undefined}
          weightUnit={weightUnit}
          // Fadiga do grupo: a MESMA consulta `last-session-sets` que a
          // execução já faz para este exercício (sem request novo). Enquanto
          // carrega, `undefined` esconde o chip; sem leitura, "indisponível".
          groupFatigue={currentGroupFatigue}
          // Mesmos fluxos e confirmações da execução: troca antes da 1ª série
          // pelo `EquipmentSubstituteModal`; pular pelo `SkipExerciseModal`.
          onSwap={!current.loggedRow ? openSubstitute : undefined}
          onSkip={() => openSkipModalFor(currentWorkoutPlanExerciseId)}
          nextExercise={
            nextPendingItem
              ? { name: nextPendingItem.planExercise.exercise_name, muscle: nextPendingItem.planExercise.muscle_primary }
              : null
          }
          substitution={
            override
              ? {
                  exerciseName: effectiveExerciseName,
                  equipmentName: effectiveEquipmentName,
                  equipmentPhotoUrl: currentEquipmentPhotoUrl,
                  sameExercise: override.mapping_id === current.planExercise.mapping_id,
                  setsCount: current.targetCount,
                  repsTarget: effectiveRepsTarget,
                  loadTarget: effectiveLoadTarget,
                  rpeTarget: effectiveRpeTarget,
                }
              : null
          }
          onStart={() => {
            if (!currentKey) return;
            const nextBriefed = new Set(briefedKeys).add(currentKey);
            setBriefedKeys(nextBriefed);
            persistSession({ briefed_keys: Array.from(nextBriefed) });
          }}
        />
      ) : showWarmup ? (
        <WarmupCard
          weightUnit={weightUnit}
          exerciseName={effectiveExerciseName}
          equipmentName={effectiveEquipmentName}
          equipmentPhotoUrl={currentEquipmentPhotoUrl}
          referenceWorkingLoad={repsDefaultWeight}
          loadContext={sessionLoadContext}
          state={currentWarmupState}
          saving={savePreparatorySetMutation.isPending || ensureExerciseMutation.isPending}
          onMark={handleWarmupMark}
          onSkipAll={() => updateWarmup({ first: 'skipped', second: 'skipped' })}
        />
      ) : applicationStage && applicationProfile && applicationPlanned && advancedTechniqueValue ? (
        // Modo Intensivo — etapa do drop set / rest-pause: etapa em destaque,
        // carga do protocolo, ajuste imediato por fadiga, reps com um toque e
        // RPE-alvo automático. Sem descanso do item entre as etapas.
        <IntensiveApplicationCard
          key={applicationKey}
          profile={applicationProfile}
          stageLabel={intensiveApplicationLabel(applicationNoun, applicationStage.index, applicationStage.total)}
          stageNoun={applicationNoun}
          planned={applicationPlanned}
          weightUnit={weightUnit}
          loadIncrementKg={loadIncrementKg}
          loadSuffix={intensiveLoadSuffix}
          appliedItems={describeAppliedRows(advancedTechniqueValue, techniqueBlockRows)}
          pause={applicationPause}
          saving={saveApplicationMutation.isPending || ensureExerciseMutation.isPending}
          error={saveApplicationMutation.isError ? errorMessage(saveApplicationMutation.error, SAVE_SET_ERROR) : null}
          canUndo={applicationAdjust?.key === applicationKey && applicationAdjust.undo.length > 0}
          onAdjust={(kg, source) =>
            setApplicationAdjust((prev) => adjustApplicationLoad(prev, applicationKey, applicationPlanned, kg, source))
          }
          onUndo={() => setApplicationAdjust((prev) => undoApplicationLoad(prev, applicationKey))}
          onComplete={handleApplicationComplete}
          onSkipApplication={() => {
            setApplicationAdjust(null);
            advanceLocalAdvancedStage('technique', 1, false, { skipRest: true });
          }}
          iconActions={[
            {
              key: 'swap',
              label: 'Trocar exercício',
              caption: 'Trocar',
              icon: <IconArrowsExchange size={20} />,
              testId: 'intensive-application-swap',
              onClick: () => {
                setSwapRemainingMode(Boolean(current.loggedRow));
                openSubstitute();
              },
            },
            {
              key: 'skip',
              label: 'Pular exercício',
              caption: 'Pular',
              icon: <IconPlayerSkipForward size={20} />,
              testId: 'intensive-application-skip-exercise',
              onClick: () => openSkipModalFor(currentWorkoutPlanExerciseId),
            },
            {
              key: 'end',
              label: 'Encerrar técnica',
              caption: 'Encerrar',
              icon: <IconPlayerStop size={20} />,
              testId: 'intensive-application-end-technique',
              onClick: () => {
                setApplicationAdjust(null);
                setTechniquePause(null);
                advanceLocalAdvancedStage('technique', remainingTechniqueApplications(applicationStage), false, {
                  skipRest: true,
                });
              },
            },
          ]}
          beforeActions={nextExerciseLine}
        />
      ) : advancedStage?.kind === 'technique' && advancedTechniqueValue ? (
        // Fase 5 — uma aplicação da técnica (mini-bloco de rest-pause, drop),
        // nos DOIS modos: reps/RPE/carga editáveis, meta "até a falha", o
        // equivalente em séries ao vivo (mesma regra do backend) e o vínculo
        // com a série âncora no envio.
        <SetLoggerCard
          exerciseName={effectiveExerciseName}
          goalBadge={currentGoalBadge}
          description={effectiveExerciseDescription}
          exerciseCard={effectiveExerciseCard}
          equipmentName={effectiveEquipmentName}
          equipmentPhotoUrl={currentEquipmentPhotoUrl}
          muscleGroup={current.planExercise.muscle_primary}
          muscleSecondaryGroup={current.planExercise.muscle_secondary}
          setLabel={techniqueStageLabel(
            advancedTechniqueValue,
            getAdvancedTechniqueLabel(advancedTechniqueValue),
            advancedStage.index,
            advancedStage.total,
          )}
          defaultReps={null}
          defaultWeight={techniqueApplicationDefaultWeight ?? techniqueAnchorWeight}
          defaultRpe={techniqueAnchorRpe ?? defaultRpe}
          previousSessionSets={null}
          weightUnit={weightUnit}
          onWeightUnitChange={setWeightUnit}
          loadIncrementKg={loadIncrementKg}
          saving={saveSetMutation.isPending || ensureExerciseMutation.isPending}
          onSave={(values) =>
            submitSet({
              ...values,
              technique_code: advancedTechniqueValue,
              anchor_set_number: techniqueAnchorSetNumber,
            })
          }
          saveError={saveSetMutation.isError ? errorMessage(saveSetMutation.error, SAVE_SET_ERROR) : null}
          currentGroupFatigue={currentGroupFatigue}
          onSkipExercise={() => openSkipModalFor(currentWorkoutPlanExerciseId)}
          onSkip={() => advanceLocalAdvancedStage('technique')}
          onEndTechnique={() =>
            advanceLocalAdvancedStage('technique', remainingTechniqueApplications(advancedStage))
          }
          onFinishExercise={openFinishModal}
          setType={getAdvancedTechniqueSetType(advancedTechniqueValue)}
          techniqueDescription={getAdvancedTechniqueDescription(advancedTechniqueValue)}
          techniqueGoalLabel={techniqueGoalLabel(techniqueAnchorRpe)}
          techniqueAppliedItems={describeAppliedRows(advancedTechniqueValue, techniqueBlockRows)}
          techniqueEquivalent={(reps, rpe) =>
            previewTechniqueApplication({
              catalog: techniqueCatalog,
              techniqueCode: advancedTechniqueValue,
              anchor: techniqueAnchor,
              previousApplications: techniqueBlockRows.map((row) => toTechniqueApplication(row, advancedTechniqueValue)),
              reps,
              rpe,
              repsTarget: effectiveRepsTarget,
            })
          }
          staticRestSeconds={current.planExercise.advanced_technique_duration_seconds ?? null}
          variant="techniqueApplication"
          showMuscleMap={false}
          beforeActions={nextExerciseLine}
        />
      ) : advancedStage ? (
        // Sub-etapas reais de aquecimento/ajuste/aplicação da técnica do
        // modo "Fazer treino avançado", reaproveitando o mesmo
        // `SetLoggerCard` visual. "technique" é gravada via
        // `saveSetMutation` (igual a uma série de trabalho normal);
        // "warmup"/"feeler" são gravadas via `savePreparatorySetMutation`
        // (`set_type` `warmup`/`feeler`, carga/reps executadas) e só depois
        // avançam o contador local. "Pular" continua só local.
        <SetLoggerCard
          exerciseName={effectiveExerciseName}
          goalBadge={currentGoalBadge}
          description={effectiveExerciseDescription}
          exerciseCard={effectiveExerciseCard}
          equipmentName={effectiveEquipmentName}
          equipmentPhotoUrl={currentEquipmentPhotoUrl}
          muscleGroup={current.planExercise.muscle_primary}
          muscleSecondaryGroup={current.planExercise.muscle_secondary}
          setLabel={
            advancedStage.kind === 'warmup'
              ? `Aquecimento ${advancedStage.index}/${advancedStage.total}`
              : advancedStage.kind === 'feeler'
                ? `Série de ajuste ${advancedStage.index}/${advancedStage.total}`
                : `${getAdvancedTechniqueApplicationNoun(advancedTechniqueValue ?? '')} ${advancedStage.index}/${advancedStage.total}`
          }
          repsTarget={
            advancedStage.kind === 'warmup'
              ? current.planExercise.warmup_reps != null
                ? String(current.planExercise.warmup_reps)
                : null
              : advancedStage.kind === 'feeler'
                ? current.planExercise.feeler_set_reps != null
                  ? String(current.planExercise.feeler_set_reps)
                  : null
                : effectiveRepsTarget
          }
          loadTarget={effectiveLoadTarget}
          rpeTarget={effectiveRpeTarget}
          defaultReps={
            advancedStage.kind === 'warmup'
              ? (current.planExercise.warmup_reps ?? defaultReps)
              : advancedStage.kind === 'feeler'
                ? (current.planExercise.feeler_set_reps ?? defaultReps)
                : defaultReps
          }
          defaultWeight={advancedStageDefaultWeight}
          defaultRpe={defaultRpe}
          previousSessionSets={null}
          weightUnit={weightUnit}
          onWeightUnitChange={setWeightUnit}
          loadIncrementKg={loadIncrementKg}
          saving={
            advancedStage.kind === 'technique'
              ? saveSetMutation.isPending || ensureExerciseMutation.isPending
              : savePreparatorySetMutation.isPending || ensureExerciseMutation.isPending
          }
          onSave={(values) => {
            if (advancedStage.kind === 'warmup' || advancedStage.kind === 'feeler') {
              if (savePreparatorySetMutation.isPending || !savePreparatoryLatchRef.current.tryEnter()) return;
              const kind = advancedStage.kind;
              savePreparatorySetMutation.mutate(
                {
                  set_type: kind,
                  reps: values.reps,
                  weight_kg: values.weight_kg,
                  rpe: values.rpe,
                  muscle_pain: values.muscle_pain,
                  joint_pain: values.joint_pain,
                  notes: values.notes,
                },
                { onSuccess: () => advanceLocalAdvancedStage(kind, 1, true) },
              );
              return;
            }
            submitSet(values);
          }}
          saveError={
            advancedStage.kind === 'technique'
              ? saveSetMutation.isError
                ? errorMessage(saveSetMutation.error, SAVE_SET_ERROR)
                : null
              : savePreparatorySetMutation.isError
                ? errorMessage(savePreparatorySetMutation.error, SAVE_PREPARATORY_ERROR)
                : null
          }
          currentGroupFatigue={currentGroupFatigue}
          onSkipExercise={() => openSkipModalFor(currentWorkoutPlanExerciseId)}
          onSkip={() => advanceLocalAdvancedStage(advancedStage.kind)}
          // Troca "limpa" (override) também durante aquecimento/ajuste
          // enquanto nada foi gravado — preserva aquecimento -> ajuste -> trabalho.
          onSubstituteClick={!current.loggedRow ? openSubstitute : undefined}
          onFinishExercise={openFinishModal}
          variant={advancedStageVariant}
          showMuscleMap={false}
          beforeActions={nextExerciseLine}
        />
      ) : (
        <>
          {showAdjustedSetsBadge && (
            <Badge size="sm" variant="light" color="orange" style={{ alignSelf: 'flex-start' }}>
              Meta ajustada: {adjustedSetsCount} série(s) (era {originalSetsCount}) para compensar o novo equipamento
            </Badge>
          )}
          <SetLoggerCard
            exerciseName={effectiveExerciseName}
            goalBadge={currentGoalBadge}
            description={effectiveExerciseDescription}
            exerciseCard={effectiveExerciseCard}
            equipmentName={effectiveEquipmentName}
            equipmentPhotoUrl={currentEquipmentPhotoUrl}
            muscleGroup={current.planExercise.muscle_primary}
            muscleSecondaryGroup={current.planExercise.muscle_secondary}
            setLabel={measureSetLabel(`Série ${workingSetNumber} de ${current.targetCount}`, measureSpec)}
            repsTarget={effectiveRepsTarget}
            loadTarget={effectiveLoadTarget}
            // Fix M97: faixa de força por %1RM congelada (pronta do backend).
            loadRangeText={formatStrengthLoadRange(prescription?.strength_load, weightUnit)}
            rpeTarget={effectiveRpeTarget}
            defaultReps={defaultReps}
            defaultWeight={defaultWeight}
            measure={measureSpec.measure}
            durationSecondsTarget={measureSpec.durationSecondsTarget}
            timeWindowSeconds={measureSpec.timeWindowSeconds}
            defaultDurationSeconds={measureDefaults.durationSeconds}
            // Era RPE 10 FIXO aqui (§5.7 proíbe: onde a falha não é
            // permitida a ficha nunca mostra 10). O default do modo avançado
            // passa a ser o alvo efetivo da prescrição congelada, que já vem
            // clampado pelo teto seguro do par.
            defaultRpe={hasAdvancedFlow ? advancedWorkingDefaultRpe : defaultRpe}
            // Histórico só depois de a consulta responder: enquanto carrega (ou
            // se ela falhar) o card some, em vez de afirmar "Ainda não há
            // execução comparável".
            previousSessionSets={previousSessionSetsQuery.isSuccess ? previousSessionSets : null}
            previousSessionRecord={previousSessionRecord}
            currentGroupFatigue={currentGroupFatigue}
            previousSessionHighlight={workingSetNumber}
            weightUnit={weightUnit}
            onWeightUnitChange={setWeightUnit}
            loadIncrementKg={loadIncrementKg}
            progression={
              workingSetNumber === 1 ? current.planExercise.progression : undefined
            }
            progressionNote={
              workingSetNumber === 1 ? current.planExercise.progression_note : undefined
            }
            // Lote 6/P9: decisão vigente do motor e status da série anterior.
            engineDecision={prescription?.decision ?? null}
            lastSetStatus={lastSavedSet && lastSavedSet.forKey === currentKey ? lastSavedSet.saved : null}
            saving={saveSetMutation.isPending || ensureExerciseMutation.isPending}
            saveLabel="Finalizar série"
            onSave={submitSet}
            saveError={saveSetMutation.isError ? errorMessage(saveSetMutation.error, SAVE_SET_ERROR) : null}
            onSkipExercise={() => openSkipModalFor(currentWorkoutPlanExerciseId)}
            // "Dica da IA": só com o toggle ligado e a avaliação real desta série.
            perSetAiEnabled={perSetAiEnabled}
            aiEvaluation={suggestion}
            // Troca "limpa" (override) só antes da 1ª série: depois disso o
            // caminho correto é encerrar o exercício e continuar com o
            // equipamento novo pelas séries restantes (FinishExerciseModal).
            onSubstituteClick={!current.loggedRow ? openSubstitute : undefined}
            onFinishExercise={openFinishModal}
            peakContractionSeconds={current.planExercise.peak_contraction_seconds}
            variant={workingSetVariant}
            // M102: reps/RPE por lado (E/D) em exercício unilateral.
            unilateral={currentIsUnilateral}
            // O mapa muscular fica DENTRO do card (fileira "Mais opções |
            // Músculos"), acima do rodapé de ação.
            showMuscleMap={showMuscleMap}
            beforeActions={nextExerciseLine}
            upcomingTechnique={advancedTechniqueValue}
            upcomingTechniqueApplications={advancedApplicationsTotal}
            // Família além da falha: "reps extras" só na ÚLTIMA série de trabalho.
            extraRepsEnabled={isExtraRepsTechnique(advancedTechniqueValue) && workingSetNumber >= current.targetCount}
          />
        </>
      )}

      {/* Com card de série, o "Próximo" vai dentro dele, acima do rodapé; no
          briefing ele fica no próprio briefing (sem duplicar). */}
      {loggerVariant == null && nextExerciseLine}

      {/* Com o card de série na tela o erro aparece junto do CTA dele
          (`saveError`); aqui só fora dele (descanso, aquecimento genérico). */}
      {loggerVariant == null && saveSetMutation.isError && (
        <Alert color="red" variant="light" icon={<IconAlertTriangle size={16} />}>
          {errorMessage(saveSetMutation.error, SAVE_SET_ERROR)}
        </Alert>
      )}
      {loggerVariant == null && savePreparatorySetMutation.isError && (
        <Alert color="red" variant="light" icon={<IconAlertTriangle size={16} />}>
          {errorMessage(savePreparatorySetMutation.error, SAVE_PREPARATORY_ERROR)}
        </Alert>
      )}
      {!resting && finishMutation.isError && (
        <Alert color="red" variant="light" icon={<IconAlertTriangle size={16} />}>
          {errorMessage(finishMutation.error, 'Não foi possível finalizar o treino.')}
        </Alert>
      )}

      <SkipExerciseModal
        opened={skipModalOpened}
        onClose={() => {
          closeSkipModal();
          setSkipTargetId(null);
        }}
        items={skipModalItems}
        onSelect={handleSkipSelect}
      />

      <EquipmentSubstituteModal
        opened={substituteOpen}
        onClose={() => {
          closeSubstitute();
          setSwapRemainingMode(false);
        }}
        gymId={session.gym_id}
        mappingId={effectiveMappingId}
        primaryGroup={current.planExercise.muscle_primary}
        currentEquipmentLinkId={effectiveEquipmentLinkId}
        requiredTechnique={current.planExercise.advanced_technique ?? null}
        mandatoryChoice={needsEquipmentChoice}
        submitting={swappingEquipment}
        onSelect={handleSubstituteSelect}
      />

      <FinishExerciseModal
        opened={finishModalOpened}
        onClose={closeFinishModal}
        remainingSets={Math.max(0, current.targetCount - current.loggedWorkingCount)}
        onGoToNext={() => {
          closeFinishModal();
          openSkipModalFor(currentWorkoutPlanExerciseId);
        }}
        onSwapEquipment={() => {
          closeFinishModal();
          setSwapRemainingMode(true);
          openSubstitute();
        }}
      />

      {pendingFinishModal}
    </Stack>
  );
}
