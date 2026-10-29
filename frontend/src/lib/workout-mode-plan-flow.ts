// Lógica PURA do fluxo de execução de ficha do Modo Treino
// (`modo-treino/PlanExecutionScreen.tsx`), extraída para ser testável sem
// DOM/React (vitest em environment "node"). Nada de `apiFetch`/`window`: além
// de tipos, só o piso de séries do dono (`prescription-defaults.ts`, que lê
// `motor-constants.generated.json`).

import { MIN_SETS_COUNT } from './prescription-defaults';
import type {
  AdvancedLocalStageProgress,
  ExerciseLogRow,
  GymEquipmentLink,
  MidExerciseExtra,
  StoredWorkoutModeSession,
  SuggestedProgressionResponse,
  WorkoutModeEquipmentOverride,
  WorkoutPlanExercise,
} from './workout-mode-types';
import type { SessionLoadPlan } from './workout-mode-load-suggestions';

// ---------------------------------------------------------------------------
// Teto de RPE e passo de carga do PAR em execução (M76, Lote 1b — C5/C7)
// ---------------------------------------------------------------------------

export type SessionPairLimits = {
  /** Teto de RPE FINAL composto pelo servidor (segurança do par + recuperação). */
  rpeCeiling: number | null;
  /** Passo de carga do equipamento efetivo (`load_increment_kg` da API). */
  loadIncrementKg: number | null;
  /** Cargas de aquecimento/ajuste/técnica já calculadas pela API (só no par da ficha). */
  loadPlan: SessionLoadPlan | null;
};

/**
 * De onde vêm o teto e o passo do par que está sendo executado. A tela NÃO
 * compõe tetos (nada de `min` entre prescrição e overlay): lê o valor final
 * que o servidor já compôs.
 *
 * - Equipamento/exercício trocado nesta sessão (`hasOverride`): o par mudou,
 *   a prescrição da ficha não vale mais -> `GET /exercises/suggested-progression`
 *   do par novo (motor v2 com segurança + recuperação projetada).
 * - Continuação sintética (troca de equipamento no meio do exercício): o que
 *   a mesma API devolveu na hora da troca, guardado na continuação.
 * - Item da ficha: a prescrição EXIBIDA (snapshot + overlay de hoje, que só
 *   restringe); sem snapshot (ficha anterior ao PR-11), o overlay do par.
 */
export function resolveSessionPairLimits(params: {
  planExercise: WorkoutPlanExercise;
  hasOverride: boolean;
  pairSuggestion: SuggestedProgressionResponse | null | undefined;
  syntheticExtra: MidExerciseExtra | null | undefined;
}): SessionPairLimits {
  const { planExercise, hasOverride, pairSuggestion, syntheticExtra } = params;
  if (hasOverride) {
    return {
      rpeCeiling: pairSuggestion?.effective_rpe_ceiling ?? null,
      loadIncrementKg: pairSuggestion?.load_increment_kg ?? null,
      loadPlan: null,
    };
  }
  if (syntheticExtra) {
    return {
      rpeCeiling: syntheticExtra.effective_rpe_ceiling ?? null,
      loadIncrementKg: syntheticExtra.load_increment_kg ?? null,
      loadPlan: null,
    };
  }
  const prescription = planExercise.prescription ?? null;
  return {
    rpeCeiling: prescription?.effective_rpe_ceiling ?? planExercise.safety_overlay?.effective_rpe_ceiling ?? null,
    loadIncrementKg: planExercise.load_increment_kg ?? null,
    loadPlan: prescription,
  };
}

// ---------------------------------------------------------------------------
// Próximo exercício
// ---------------------------------------------------------------------------

export type UpcomingCandidate = { done: boolean };

/**
 * Próximo exercício PENDENTE da fila, ignorando o exercício identificado por
 * `excludeKey`.
 *
 * - Fora da transição, `excludeKey` é o exercício atual: devolve o pendente
 *   seguinte a ele.
 * - Na transição entre exercícios (descanso após a ÚLTIMA série),
 *   `excludeKey` precisa ser o exercício RECÉM-ENCERRADO, não o `current`
 *   ao vivo: a série é gravada e a activity é refeita ANTES de abrir o
 *   descanso, então nesse ponto o `current` derivado do servidor já é o
 *   próximo exercício. Usar "o pendente depois do `current`" ali mostrava o
 *   exercício seguinte ao próximo (o 2º parecia ser pulado).
 *
 * `items` já vem na ordem efetiva (skip/prioridade aplicados), então o
 * primeiro pendente que não é o excluído é o próximo real.
 */
export function findUpcomingExercise<T extends UpcomingCandidate>(
  items: ReadonlyArray<T>,
  keyOf: (item: T) => string | null,
  excludeKey: string | null,
): T | null {
  return items.find((item) => isUpcomingCandidate(item, keyOf, excludeKey)) ?? null;
}

function isUpcomingCandidate<T extends UpcomingCandidate>(
  item: T,
  keyOf: (item: T) => string | null,
  excludeKey: string | null,
): boolean {
  return !item.done && (excludeKey == null || keyOf(item) !== excludeKey);
}

export type RemainingExercise<T> = {
  item: T;
  /** Ordinal de execução ("Exercício N de T") que o item terá quando chegar a vez dele. */
  ordinal: number;
};

/**
 * M64: exercícios que vêm DEPOIS do próximo (`findUpcomingExercise`), na
 * ordem em que o Modo Treino vai apresentá-los — lista exibida na tela de
 * pausa de transição, abaixo do card "A seguir".
 *
 * Usa exatamente o mesmo predicado e a mesma ordem efetiva de
 * `findUpcomingExercise` (skip/prioridade/continuações de troca já aplicados
 * em `items`), descartando o primeiro candidato — que É o próximo exibido no
 * card —, então a lista nunca diverge do que vem de fato. Concluídos e o
 * exercício excluído (o atual / o recém-encerrado) ficam de fora.
 *
 * `ordinal`: tudo que não está na lista (concluídos, excluído e o próximo)
 * é executado antes dela, então o i-ésimo restante é o
 * `items.length - restantes + i + 1`-ésimo exercício da sessão.
 */
export function findRemainingAfterUpcoming<T extends UpcomingCandidate>(
  items: ReadonlyArray<T>,
  keyOf: (item: T) => string | null,
  excludeKey: string | null,
): RemainingExercise<T>[] {
  const remaining = items.filter((item) => isUpcomingCandidate(item, keyOf, excludeKey)).slice(1);
  const firstOrdinal = items.length - remaining.length + 1;
  return remaining.map((item, index) => ({ item, ordinal: firstOrdinal + index }));
}

// ---------------------------------------------------------------------------
// Troca de equipamento
// ---------------------------------------------------------------------------

// Campos de configuração avançada (aquecimento/ajuste/técnica/pico de
// contração) de um item de ficha — herdados pela continuação sintética.
const ADVANCED_FIELDS = [
  'advanced_technique',
  'advanced_technique_applications',
  'advanced_technique_duration_seconds',
  'warmup_sets_count',
  'warmup_reps',
  'warmup_rest_seconds',
  'feeler_set_sets_count',
  'feeler_set_reps',
  'feeler_set_rest_seconds',
  'peak_contraction_seconds',
  // Lote T5 (medidas de série): a continuação de um item por tempo/máximo de
  // reps continua sendo por tempo/máximo (ausente = reps, como sempre).
  'measure',
  'duration_seconds_target',
  'time_window_seconds',
] as const;

export type AdvancedExerciseConfig = Pick<WorkoutPlanExercise, (typeof ADVANCED_FIELDS)[number]>;

export function pickAdvancedConfig(planExercise: Partial<WorkoutPlanExercise>): AdvancedExerciseConfig {
  const config: AdvancedExerciseConfig = {};
  for (const field of ADVANCED_FIELDS) {
    const value = planExercise[field];
    if (value != null) (config as Record<string, unknown>)[field] = value;
  }
  return config;
}

/**
 * "Trocar de equipamento (séries restantes)" só precisa encerrar o item e
 * criar uma continuação sintética quando o item atual JÁ tem exercício
 * gravado no backend (o `exercises` criado aponta para o equipamento antigo).
 * Sem nada gravado — inclusive quando o usuário ainda está no aquecimento ou
 * na série de ajuste — basta um override no próprio item da ficha, que
 * preserva toda a configuração e o progresso de aquecimento/ajuste.
 */
export function shouldSwapAsOverride(params: { hasLoggedRow: boolean; workoutPlanExerciseId: number | null }): boolean {
  const { hasLoggedRow, workoutPlanExerciseId } = params;
  return !hasLoggedRow && workoutPlanExerciseId != null && workoutPlanExerciseId > 0;
}

/**
 * Progresso local do modo avançado de um item logo após trocar o
 * equipamento: o aquecimento é do GRUPO MUSCULAR (o que já foi feito/pulado
 * continua valendo), mas a série de ajuste é calibração de carga do
 * EQUIPAMENTO — volta a ser oferecida com o equipamento novo. Nunca é pulada
 * silenciosamente por causa da troca.
 */
export function progressAfterEquipmentSwap(effective: { warmupDone: number }): AdvancedLocalStageProgress {
  return { warmupDone: Math.max(0, effective.warmupDone), feelerDone: 0, techniqueSkipped: 0 };
}

/**
 * Id do item ORIGINAL da ficha: o próprio id quando é de uma linha da ficha;
 * numa continuação sintética (id negativo), o item que ela substitui.
 */
export function originalPlanItemId(
  planItemId: number | null | undefined,
  extras: ReadonlyArray<Pick<MidExerciseExtra, 'localId' | 'originalWorkoutPlanExerciseId'>>,
): number | null {
  if (planItemId == null) return null;
  if (planItemId > 0) return planItemId;
  return extras.find((extra) => extra.localId === planItemId)?.originalWorkoutPlanExerciseId ?? null;
}

/**
 * Monta a continuação sintética de um exercício encerrado no meio trocando
 * de equipamento. Herda a configuração avançada do item original
 * (aquecimento/ajuste/técnica/pico de contração) — antes a continuação era
 * criada sem esses campos e o fluxo ia direto para as séries de trabalho,
 * pulando a série de ajuste com o equipamento novo.
 */
export function buildSyntheticContinuation(params: {
  localId: number;
  current: WorkoutPlanExercise;
  /** Séries de trabalho que faltam; abaixo do piso `MIN_SETS_COUNT` vale o piso. */
  remainingSets: number;
  choice: { exercise_name: string; mapping_id: number; equipment_link_id: number; equipment_name: string };
  targets: {
    reps_target: string | null;
    load_target: string | null;
    rpe_target: string | null;
    // Do `suggested-progression` do par novo (M76 Lote 1b); null = API não respondeu.
    load_increment_kg?: number | null;
    effective_rpe_ceiling?: number | null;
    failure_allowed?: boolean | null;
  };
  existingExtras: ReadonlyArray<MidExerciseExtra>;
}): MidExerciseExtra {
  const { localId, current, remainingSets, choice, targets, existingExtras } = params;
  // Trocar de novo a partir de uma continuação sintética mantém o vínculo com
  // o item ORIGINAL da ficha.
  const originalWorkoutPlanExerciseId = originalPlanItemId(current.workout_plan_exercise_id, existingExtras);
  return {
    localId,
    exercise_name: choice.exercise_name,
    mapping_id: choice.mapping_id,
    equipment_link_id: choice.equipment_link_id,
    equipment_name: choice.equipment_name,
    muscle_primary: current.muscle_primary,
    muscle_secondary: current.muscle_secondary,
    sets_count: Math.max(MIN_SETS_COUNT, remainingSets),
    reps_target: targets.reps_target,
    load_target: targets.load_target,
    rpe_target: targets.rpe_target,
    rest_seconds: current.rest_seconds,
    load_increment_kg: targets.load_increment_kg ?? null,
    effective_rpe_ceiling: targets.effective_rpe_ceiling ?? null,
    failure_allowed: targets.failure_allowed ?? null,
    originalWorkoutPlanExerciseId,
    ...pickAdvancedConfig(current),
  };
}

/** Item de ficha "virtual" que representa uma continuação sintética na fila. */
export function syntheticExtraToPlanExercise(extra: MidExerciseExtra): WorkoutPlanExercise {
  return {
    ...pickAdvancedConfig(extra),
    workout_plan_exercise_id: extra.localId,
    sequence: 9999,
    exercise_name: extra.exercise_name,
    mapping_id: extra.mapping_id,
    muscle_primary: extra.muscle_primary,
    muscle_secondary: extra.muscle_secondary,
    equipment_name: extra.equipment_name,
    preferred_equipment_id: extra.equipment_link_id,
    sets_count: extra.sets_count,
    reps_target: extra.reps_target,
    load_target: extra.load_target,
    rpe_target: extra.rpe_target,
    rest_seconds: extra.rest_seconds,
    load_increment_kg: extra.load_increment_kg ?? null,
  } as WorkoutPlanExercise;
}

// ---------------------------------------------------------------------------
// Retomada de sessão
// ---------------------------------------------------------------------------

/**
 * Retomar uma sessão em andamento a partir do servidor
 * (`GET /activities?session_status=in_progress`) só traz os campos da
 * activity — `advanced_mode`, progresso de aquecimento/ajuste pulados,
 * overrides de equipamento, skips etc. vivem só no cliente. Se existe um
 * snapshot local guardado ao sair do modo treino para a MESMA sessão, ele é
 * a base; os campos vindos do servidor prevalecem por cima (são a fonte de
 * verdade da activity). Snapshot de outra sessão é ignorado.
 */
export function mergeResumedSession(
  base: StoredWorkoutModeSession,
  snapshot: StoredWorkoutModeSession | null | undefined,
): StoredWorkoutModeSession {
  if (!snapshot || snapshot.session_id !== base.session_id) return base;
  const serverFields = Object.fromEntries(Object.entries(base).filter(([, value]) => value !== undefined));
  return { ...snapshot, ...serverFields } as StoredWorkoutModeSession;
}

// ---------------------------------------------------------------------------
// Fila de execução
// ---------------------------------------------------------------------------

/**
 * Fila de exercícios da execução: itens da ficha + continuações sintéticas,
 * sem os cancelados, na ordem de `sequence`.
 *
 * Em "Fazer treino avançado" a fila é a ficha INTEIRA: exercícios sem
 * aquecimento/ajuste/técnica/pico de contração seguem com séries normais.
 * Antes eles eram filtrados, e o último item da ficha 110 (produção,
 * 2026-09-17, extensão de tronco no banco romano, sem configuração avançada)
 * sumiu da tela, contando "7 de 7" numa ficha de 8 itens e encerrando o
 * treino sem ele.
 */
export function buildExecutionQueue<T extends WorkoutPlanExercise>(params: {
  planExercises: ReadonlyArray<T>;
  syntheticExercises: ReadonlyArray<T>;
  cancelledIds: ReadonlySet<number>;
}): T[] {
  const { planExercises, syntheticExercises, cancelledIds } = params;
  return [...planExercises, ...syntheticExercises]
    .filter((ex) => ex.workout_plan_exercise_id == null || !cancelledIds.has(ex.workout_plan_exercise_id))
    .sort((a, b) => a.sequence - b.sequence);
}

/**
 * Reordena apenas as posições ainda PENDENTES da fila: o item escolhido no
 * `SkipExerciseModal` (`priorityId`) vem primeiro; entre o restante, os não
 * pulados preservam a ordem original e os pulados vão para o fim da fila.
 * Itens já concluídos não mudam de posição. Recalculado a cada render a
 * partir do estado real do servidor (`done`), nunca de um índice acumulado.
 *
 * Vive aqui (e não mais dentro de `PlanExecutionScreen`) para a fila real do
 * modo treino poder ser EXECUTADA em teste, e não só lida: a ordem de
 * exibição é o que decide qual item da ficha recebe cada lançamento.
 */
export function reorderPendingBySkip<T extends { done: boolean }>(
  items: ReadonlyArray<T>,
  keyOf: (item: T) => number,
  skippedIds: ReadonlySet<number>,
  priorityId: number | null,
): T[] {
  const pendingSlots = items.map((item, idx) => ({ item, idx })).filter((slot) => !slot.item.done);
  if (pendingSlots.length <= 1) return [...items];

  const priority = pendingSlots.filter((slot) => priorityId != null && keyOf(slot.item) === priorityId);
  const rest = pendingSlots.filter((slot) => priorityId == null || keyOf(slot.item) !== priorityId);
  const notSkipped = rest.filter((slot) => !skippedIds.has(keyOf(slot.item)));
  const skipped = rest.filter((slot) => skippedIds.has(keyOf(slot.item)));
  const newPendingOrder = [...priority, ...notSkipped, ...skipped].map((slot) => slot.item);

  const result = [...items];
  pendingSlots.forEach((slot, i) => {
    result[slot.idx] = newPendingOrder[i];
  });
  return result;
}

// ---------------------------------------------------------------------------
// Sub-etapas do treino avançado (aquecimento -> ajuste -> trabalho -> técnica)
// ---------------------------------------------------------------------------

/**
 * Sub-etapa real do modo "Fazer treino avançado". `index`/`total` são
 * 1-based, usados no rótulo "Aquecimento 1/2", "Mini-bloco 2/3" etc.
 * `null` = série de TRABALHO normal ("Série N de S").
 */
export type AdvancedSubStage =
  | { kind: 'warmup'; index: number; total: number }
  | { kind: 'feeler'; index: number; total: number }
  | { kind: 'technique'; index: number; total: number };

/**
 * Contadores de um exercício no treino avançado. Os alvos (`*Target`) já vêm
 * zerados quando a sub-etapa não existe para o item (sem aquecimento/ajuste
 * configurado, sem técnica, técnica da família "além da falha"). Os `*Done`
 * de aquecimento/ajuste são o progresso EFETIVO (maior entre o contador
 * local, que inclui as puladas, e o que já está gravado).
 */
export type AdvancedExerciseCounters = {
  /** Séries de trabalho da ficha. */
  workingTarget: number;
  /** Séries gravadas que NÃO são aquecimento/ajuste (trabalho + aplicações da técnica). */
  loggedNonPreparatory: number;
  warmupTarget: number;
  warmupDone: number;
  feelerTarget: number;
  feelerDone: number;
  /** Aplicações da técnica avançada que viram sub-etapa própria. */
  techniqueTarget: number;
  /** Aplicações PULADAS localmente (nunca gravadas). */
  techniqueSkipped: number;
};

/**
 * Aplicações da técnica já RESOLVIDAS: gravadas (o que passou das séries de
 * trabalho) + puladas, limitado ao total configurado.
 */
export function countResolvedTechniqueApplications(
  counters: Pick<AdvancedExerciseCounters, 'workingTarget' | 'loggedNonPreparatory' | 'techniqueTarget' | 'techniqueSkipped'>,
): number {
  const { workingTarget, loggedNonPreparatory, techniqueTarget, techniqueSkipped } = counters;
  if (techniqueTarget <= 0) return 0;
  const persisted = Math.max(0, loggedNonPreparatory - workingTarget);
  return Math.min(techniqueTarget, persisted + Math.max(0, techniqueSkipped));
}

/** Sub-etapa a exibir agora; `null` = série de trabalho (ou nada pendente). */
export function resolveAdvancedSubStage(counters: AdvancedExerciseCounters): AdvancedSubStage | null {
  const { workingTarget, loggedNonPreparatory, warmupTarget, warmupDone, feelerTarget, feelerDone, techniqueTarget } = counters;
  if (warmupDone < warmupTarget) return { kind: 'warmup', index: warmupDone + 1, total: warmupTarget };
  if (feelerDone < feelerTarget) return { kind: 'feeler', index: feelerDone + 1, total: feelerTarget };
  if (loggedNonPreparatory < workingTarget) return null;
  const resolved = countResolvedTechniqueApplications(counters);
  if (resolved < techniqueTarget) return { kind: 'technique', index: resolved + 1, total: techniqueTarget };
  return null;
}

/**
 * Exercício concluído no treino avançado: tudo que a ficha pede foi gravado
 * OU pulado — inclusive as aplicações da técnica.
 *
 * Antes `techniqueSkipped` ficava fora da conta (só aquecimento/ajuste
 * pulados contavam): pular a ÚLTIMA aplicação deixava o exercício pendente
 * para sempre, sem sub-etapa a exibir, e a tela caía na série de trabalho
 * "Série N+1 de S". Usa a MESMA contagem de `resolveAdvancedSubStage`, então
 * "pendente" sempre tem etapa a exibir. As puladas só contam depois das
 * séries de trabalho (nunca concluem o exercício com trabalho faltando).
 */
export function isAdvancedExerciseDone(counters: AdvancedExerciseCounters): boolean {
  const { workingTarget, loggedNonPreparatory, warmupTarget, warmupDone, feelerTarget, feelerDone, techniqueTarget } = counters;
  if (loggedNonPreparatory < workingTarget) return false;
  if (warmupDone < warmupTarget || feelerDone < feelerTarget) return false;
  return countResolvedTechniqueApplications(counters) >= techniqueTarget;
}

// ---------------------------------------------------------------------------
// Descanso
// ---------------------------------------------------------------------------

/**
 * Duração do descanso exibido após gravar/pular uma etapa.
 *
 * Quando a PRÓXIMA etapa do mesmo exercício é uma aplicação da técnica
 * avançada (mini-bloco de rest-pause, por exemplo), o intervalo é o
 * configurado na técnica (`advanced_technique_duration_seconds`), e não o
 * descanso padrão da série. Antes o cronômetro usava sempre `rest_seconds`
 * (60 s) mesmo com 10 s configurados entre os mini-blocos.
 *
 * Depois de uma série de aquecimento ou de ajuste (`completedStageKind`), o
 * intervalo é o próprio da etapa (`warmup_rest_seconds` /
 * `feeler_set_rest_seconds`). Sem esse campo, cai no descanso da série
 * (Fase 7). Antes o cronômetro ignorava os dois campos.
 */
export function resolveRestSeconds(params: {
  nextStageKind: 'warmup' | 'feeler' | 'technique' | null;
  isExerciseTransition: boolean;
  /** Etapa que acabou de ser gravada/pulada e abriu este descanso; `null` = série de trabalho. */
  completedStageKind?: 'warmup' | 'feeler' | 'technique' | null;
  warmupRestSeconds?: number | null;
  feelerRestSeconds?: number | null;
  techniqueDurationSeconds?: number | null;
  restSeconds?: number | null;
  defaultRestSeconds: number;
}): number {
  const {
    nextStageKind,
    isExerciseTransition,
    completedStageKind,
    warmupRestSeconds,
    feelerRestSeconds,
    techniqueDurationSeconds,
    restSeconds,
    defaultRestSeconds,
  } = params;
  if (completedStageKind === 'warmup' && warmupRestSeconds != null && warmupRestSeconds > 0) {
    return warmupRestSeconds;
  }
  if (completedStageKind === 'feeler' && feelerRestSeconds != null && feelerRestSeconds > 0) {
    return feelerRestSeconds;
  }
  if (!isExerciseTransition && nextStageKind === 'technique' && techniqueDurationSeconds != null && techniqueDurationSeconds > 0) {
    return techniqueDurationSeconds;
  }
  return restSeconds || defaultRestSeconds;
}

// ---------------------------------------------------------------------------
// Exercício efetivo (após troca de equipamento/exercício)
// ---------------------------------------------------------------------------

/**
 * `mapping_id` do exercício REALMENTE em execução — a mesma ordem de fonte
 * de `resolveEffectiveExerciseDisplay` (override em memória > exercício já
 * gravado no backend, quando difere do item > item da ficha). Compartilhado
 * com o card ilustrado (`workout-mode-exercise-card.ts`), para nome,
 * descrição e card nunca apontarem para exercícios diferentes.
 */
export function resolveEffectiveMappingId(params: {
  planExercise: Pick<WorkoutPlanExercise, 'mapping_id'>;
  override?: Pick<WorkoutModeEquipmentOverride, 'mapping_id'> | null;
  loggedRow?: Pick<ExerciseLogRow, 'mapping_id'> | null;
}): number | null {
  const { planExercise, override, loggedRow } = params;
  const planMappingId = planExercise.mapping_id ?? null;
  if (override) return override.mapping_id;
  if (loggedRow?.mapping_id != null && loggedRow.mapping_id !== planMappingId) return loggedRow.mapping_id;
  return planMappingId;
}

/**
 * Nome e descrição do exercício REALMENTE em execução.
 *
 * Trocar o equipamento pode trocar o exercício (ex.: "Supino inclinado com
 * halteres" -> "Supino inclinado na máquina"). Antes a tela mostrava o nome
 * novo com a descrição do item original da ficha. Ordem de fonte, sempre
 * do exercício efetivo:
 * 1. override em memória (nome) + descrição da associação escolhida;
 * 2. exercício já gravado no backend (`loggedRow`, fonte de verdade ao
 *    retomar sem o override local);
 * 3. item da ficha.
 * A descrição da ficha só é usada quando o exercício efetivo é o mesmo do
 * item; sem descrição do exercício efetivo, nenhuma descrição é exibida
 * (melhor vazio do que a de outro exercício).
 */
export function resolveEffectiveExerciseDisplay(params: {
  planExercise: Pick<WorkoutPlanExercise, 'exercise_name' | 'mapping_id' | 'exercise_description'>;
  override?: WorkoutModeEquipmentOverride | null;
  loggedRow?: Pick<ExerciseLogRow, 'mapping_id' | 'original_name' | 'exercise_name' | 'exercise_description'> | null;
  gymLinks?: ReadonlyArray<Pick<GymEquipmentLink, 'id' | 'mapping_id' | 'mapping_exercise_description'>>;
  equipmentLinkId?: number | null;
}): { exerciseName: string; description: string | null } {
  const { planExercise, override, loggedRow, gymLinks = [], equipmentLinkId } = params;
  const planMappingId = planExercise.mapping_id ?? null;

  const mappingId = resolveEffectiveMappingId({ planExercise, override, loggedRow });
  let exerciseName = planExercise.exercise_name;
  if (override) {
    exerciseName = override.exercise_name || exerciseName;
  } else if (loggedRow && mappingId !== planMappingId) {
    exerciseName = loggedRow.exercise_name || loggedRow.original_name || exerciseName;
  }

  const candidates: Array<string | null | undefined> = [];
  if (loggedRow?.mapping_id != null && loggedRow.mapping_id === mappingId) {
    candidates.push(loggedRow.exercise_description);
  }
  const link = equipmentLinkId != null ? gymLinks.find((item) => item.id === equipmentLinkId) : undefined;
  if (link && link.mapping_id === mappingId) candidates.push(link.mapping_exercise_description);
  if (mappingId === planMappingId) candidates.push(planExercise.exercise_description);

  const description = candidates.find((value) => value != null && value.trim() !== '') ?? null;
  return { exerciseName, description };
}

// ---------------------------------------------------------------------------
// Item da ficha já coberto por uma substituição (bug de produção 2026-09-25)
// ---------------------------------------------------------------------------

/**
 * Itens da ficha cujo exercício JÁ foi executado nesta sessão sob OUTRO item
 * — ou seja, o usuário trocou o exercício de um item da ficha escolhendo um
 * exercício que a própria ficha já prescrevia em outro item.
 *
 * Bug real (atividade 230, ficha 119, 2026-09-25): o usuário trocou o
 * exercício do 2º item executado para "Desenvolvimento de ombros na máquina",
 * que era o item 941 da ficha (o primeiro). A troca vira um override no item
 * ONDE ela aconteceu (943), então o 941 continuou PENDENTE, voltou ao fim da
 * fila, foi executado de novo às 10:07 e o mesmo exercício ficou registrado
 * duas vezes — inflando volume/tonelagem e contaminando progressão,
 * dashboard e recordes. A tela exibindo "Exercício 1 de 7" com 6 concluídos
 * era CONSEQUÊNCIA disso: o item 941, na posição 1 da ficha, era o pendente
 * que sobrou.
 *
 * Só o servidor é consultado (nada de estado local, que não sobrevive a um
 * reload no meio do treino): um exercício lançado cujo `mapping_id` difere do
 * `planned_mapping_id` (o `mapping_id` do item da ficha a que ele está
 * vinculado) é, por definição, uma substituição. Se o exercício EFETIVAMENTE
 * executado é o mapping de outro item que ainda não tem lançamento próprio,
 * esse item está coberto e não deve voltar à fila.
 *
 * ATUALIZAÇÃO (bug de produção reinvestigado): esta função é uma MITIGAÇÃO a
 * jusante, não a correção. Ela evita a execução duplicada, mas dá o item como
 * concluído sem que ele tenha sido exibido, e não devolve a prescrição que o
 * slot errado consumiu (na atividade 230, a Elevação frontal do item 943 ficou
 * sem nenhum registro). A correção a montante é
 * `findPlanItemForSubstitutedExercise`: escolher um exercício que a ficha já
 * prescreve pula para o item dele, e nenhum lançamento nasce no slot errado.
 * Com ela, esta cobertura só age em sessões que JÁ estavam nesse estado
 * (override cruzado guardado no localStorage antes da correção) — mantida por
 * isso, e como rede de segurança para qualquer caminho de vínculo cruzado que
 * escape no futuro.
 *
 * Deliberadamente NÃO cobre um item pelo mapping de um lançamento que está no
 * seu próprio slot (`mapping_id === planned_mapping_id`): uma ficha com o
 * mesmo `mapping_id` em dois itens (o que a geração já impede somando os
 * dois num só, ver `_ai_selection_to_exercises`) continuaria pedindo os dois.
 * Também ignora exercícios avulsos (`workout_plan_exercise_id` nulo), que
 * nunca resolvem um item da ficha.
 */
export function findPlanItemsCoveredByAnotherSlot(params: {
  planExercises: ReadonlyArray<Pick<WorkoutPlanExercise, 'workout_plan_exercise_id' | 'mapping_id'>>;
  loggedExercises: ReadonlyArray<Pick<ExerciseLogRow, 'workout_plan_exercise_id' | 'mapping_id' | 'planned_mapping_id'>>;
}): Set<number> {
  const { planExercises, loggedExercises } = params;

  const planItemIdsWithOwnLog = new Set<number>();
  // mapping_id -> item da ficha em cujo slot a substituição foi registrada.
  const substitutedInto = new Map<number, number>();
  for (const logged of loggedExercises) {
    const slotId = logged.workout_plan_exercise_id;
    if (slotId == null) continue;
    planItemIdsWithOwnLog.add(slotId);
    const executed = logged.mapping_id;
    const planned = logged.planned_mapping_id;
    if (executed == null || planned == null || executed === planned) continue;
    if (!substitutedInto.has(executed)) substitutedInto.set(executed, slotId);
  }

  const covered = new Set<number>();
  if (substitutedInto.size === 0) return covered;
  for (const planExercise of planExercises) {
    const id = planExercise.workout_plan_exercise_id;
    const mappingId = planExercise.mapping_id;
    if (id == null || id < 0 || mappingId == null) continue;
    if (planItemIdsWithOwnLog.has(id)) continue;
    const slotId = substitutedInto.get(mappingId);
    if (slotId != null && slotId !== id) covered.add(id);
  }
  return covered;
}

/**
 * O exercício escolhido na substituição já é a prescrição de OUTRO item
 * PENDENTE da ficha? Devolve o `workout_plan_exercise_id` desse item.
 *
 * Causa real do bug de produção (atividade 230, ficha 119, 2026-09-25),
 * reproduzida em `workout-mode-plan-flow.test.ts`: o usuário pulou o item 941
 * (Desenvolvimento de ombros na máquina, equipamento ocupado), executou o 942
 * e, quando o 943 (Elevação frontal com halteres) apareceu, usou a
 * substituição de equipamento para "voltar" ao Desenvolvimento. A substituição
 * é sempre um override no item ONDE ela acontece, então a execução do
 * Desenvolvimento foi gravada no SLOT do 943 (`exercises.mapping_id=12` com
 * `workout_plan_exercise_id=943`) e o 941 continuou pendente, voltando ao fim
 * da fila e sendo executado de novo às 10:07.
 *
 * Escolher um exercício que a própria ficha já prescreve não é substituição:
 * é PULAR para aquele item. Tratando assim:
 * - o lançamento vai para o slot certo (941), com `mapping_id` igual ao
 *   `planned_mapping_id` — nada de vínculo cruzado para a progressão/dashboard
 *   interpretar como substituição;
 * - o item atual (943) continua PENDENTE e volta a ser EXIBIDO depois, em vez
 *   de ser consumido sem nunca aparecer (a prescrição da Elevação frontal
 *   simplesmente desaparecia da sessão);
 * - nenhum exercício é executado duas vezes, então nada precisa ser
 *   "coberto" depois (ver `findPlanItemsCoveredByAnotherSlot`, que continua
 *   apenas como rede de segurança para sessões que já estavam nesse estado).
 *
 * Itens sintéticos (id negativo) e itens já concluídos/cancelados não contam:
 * substituir por um exercício que já foi feito nesta sessão é uma escolha
 * legítima do usuário e segue como override normal.
 */
export function findPlanItemForSubstitutedExercise(params: {
  planExercises: ReadonlyArray<WorkoutPlanExercise>;
  chosenMappingId: number | null | undefined;
  currentWorkoutPlanExerciseId: number | null;
  isPending: (workoutPlanExerciseId: number) => boolean;
}): number | null {
  const { planExercises, chosenMappingId, currentWorkoutPlanExerciseId, isPending } = params;
  if (chosenMappingId == null) return null;
  for (const planExercise of planExercises) {
    const id = planExercise.workout_plan_exercise_id;
    if (id == null || id < 0 || id === currentWorkoutPlanExerciseId) continue;
    if (planExercise.mapping_id !== chosenMappingId) continue;
    if (!isPending(id)) continue;
    return id;
  }
  return null;
}

/**
 * Posição exibida no cabeçalho ("Exercício N de T"). `N` é o número de
 * exercícios JÁ concluídos + 1 — o ordinal do exercício que está sendo feito
 * agora —, nunca o índice do item dentro da ficha.
 *
 * Antes era `currentIndex + 1`, a posição do item na ficha: como "Pular"
 * manda o item para o FIM da fila de pendentes sem mudar sua posição, o
 * cabeçalho voltava a "Exercício 1 de 7" com 6 concluídos ao chegar num item
 * pulado no começo da ficha (print do usuário, 2026-09-25). O número
 * contradizia o selo "6 concluído(s)" e a barra de progresso ao lado, que já
 * usavam `completedCount`.
 */
export function executionPositionOrdinal(items: ReadonlyArray<{ done: boolean }>): number {
  return items.filter((item) => item.done).length + 1;
}
