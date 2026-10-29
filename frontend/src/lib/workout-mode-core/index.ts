// Núcleo compartilhado do Modo Treino (PR B da Evolução ZEPP).
//
// API PURA (sem `window`, `document`, `localStorage`, `fetch`, `Intl`,
// `process`, React ou rede) que o celular já usa e que o relógio (Zepp OS)
// passa a consumir pelo bundle `frontend/dist/workout-mode-core/workout-mode-core.js`
// (`npm run build:workout-core`, `scripts/build-workout-core.mjs`).
//
// Este arquivo só REEXPORTA: nenhuma regra nova mora aqui. Os números vêm dos
// donos no backend via `motor-constants.generated.json` /
// `set-types.generated.json` (mapa `docs/motores/FONTES_DA_VERDADE.md`, §57).
// Persistência (`workout-mode-storage.ts`) e IA (`workout-mode-ai.ts`) ficam
// FORA de propósito: cada cliente tem a sua.
//
// Garantias (vitest `src/lib/workout-mode-core.test.ts`): o bundle compila,
// não cita APIs do navegador/Node, passa `session-loads.parity.json` e o
// golden `workout-mode-core.golden.json` (mesmos casos no relógio e no
// `node --test` do repositório Zepp).

// Fila, troca de equipamento, sub-etapas do avançado e descanso.
export {
  buildExecutionQueue,
  buildSyntheticContinuation,
  countResolvedTechniqueApplications,
  executionPositionOrdinal,
  findPlanItemForSubstitutedExercise,
  isAdvancedExerciseDone,
  progressAfterEquipmentSwap,
  reorderPendingBySkip,
  resolveAdvancedSubStage,
  resolveRestSeconds,
  shouldSwapAsOverride,
  syntheticExtraToPlanExercise,
} from '../workout-mode-plan-flow';
export type { AdvancedExerciseCounters, AdvancedSubStage } from '../workout-mode-plan-flow';

// Orquestração que antes só existia na tela do celular (PR B2 da Evolução
// ZEPP): contadores do item, item coberto por troca em outro slot, fonte do
// teto de RPE/passo do par, âncora da próxima série e alvos do par trocado.
export { buildAdvancedExerciseCounters } from '../workout-mode-advanced-counters';
export type { AdvancedCountersPlanExercise } from '../workout-mode-advanced-counters';
export { findPlanItemsCoveredByAnotherSlot, resolveSessionPairLimits } from '../workout-mode-plan-flow';
export type { SessionPairLimits } from '../workout-mode-plan-flow';
export { findAnchorWorkingSet } from '../workout-mode-technique';
export type { TechniqueSetRow } from '../workout-mode-technique';
export { suggestedProgressionTargets } from '../suggested-progression';
export type { SuggestedTargets } from '../suggested-progression';

// Últimas regras que o relógio espelhava da tela (PR B3 da Evolução ZEPP):
// fluxo/sub-etapa do avançado, progresso local das sub-etapas e o seletor das
// séries de trabalho da sessão anterior (linha "Última").
export {
  advanceLocalStageProgress,
  advancedStageOf,
  effectiveAdvancedStageDone,
  hasAdvancedFlow,
  remainingTechniqueApplications,
} from '../workout-mode-advanced-stage';
export type { AdvancedFlowPlanExercise, AdvancedLocalStageKind } from '../workout-mode-advanced-stage';

// Modo do treino decidido pela ficha, sem escolha "normal/avançado" (PR B4 da
// Evolução ZEPP): aquecimento, ajuste ou técnica configurados = avançado.
export {
  exerciseHasAdvancedConfig,
  planHasAdvancedConfig,
  resolveWorkoutModeFromPlan,
} from '../workout-mode-advanced-stage';
export type { WorkoutMode } from '../workout-mode-advanced-stage';
export { selectPreviousSessionWorkingSets } from '../workout-mode-previous-session-sets';
export type { LastSessionSetsResponse, PreviousSessionSet } from '../workout-mode-types';

// Medida da série (Lote T5 das medidas de série): repetições, tempo ou máximo
// de reps — medida do item, campos/valores iniciais do registro, cronômetro de
// execução, rótulo do alvo e chaves do POST. Semântica do dono
// `api/app/domain/set_measures.py` pelo espelho `set-measures.ts`.
export {
  executionTimerSpec,
  formatSetMeasureText,
  isDurationSecondsValid,
  isMaxRepsValid,
  measureLoggerDefaults,
  measureSetLabel,
  measureTargetText,
  setMeasureOf,
  setMeasurePayload,
} from '../workout-mode-measures';
export type {
  ExecutionTimerMode,
  ExecutionTimerSpec,
  MeasureLoggerDefaults,
  MeasureLoggerDefaultsInput,
  SetMeasureSpec,
} from '../workout-mode-measures';
export { isRepBased, normalizeMeasure } from '../set-measures';

// Cargas das séries que não são de trabalho (espelho C7 de `session_loads.py`).
export {
  feelerSetLoad,
  loadStep,
  mirrorFeelerLoad,
  mirrorTechniqueApplicationLoad,
  mirrorWarmupLoads,
  roundToStep,
  techniqueApplicationLoad,
  warmupLoad,
  warmupLoads,
} from '../workout-mode-load-suggestions';
export type { SessionLoadContext, SessionLoadPlan } from '../workout-mode-load-suggestions';

// Aquecimento genérico (grupo ainda não ativado na sessão).
export {
  GENERIC_WARMUP_REPS,
  collectActivatedMuscleGroups,
  genericWarmupLoads,
  genericWarmupSet,
  needsGenericWarmup,
  resolveGenericWarmupState,
  shouldShowGenericWarmup,
} from '../workout-mode-generic-warmup';
export type { GenericWarmupSlot } from '../workout-mode-generic-warmup';

// Técnica avançada e rótulo da próxima série.
export { countWorkingSets, techniqueStageTarget } from '../workout-mode-technique';
export { resolveNextSetPreview } from '../workout-mode-next-set';
export type { NextSetPreview, NextSetPreviewInput } from '../workout-mode-next-set';

// Helpers puros de série/sessão.
export {
  clampRpeSuggestion,
  countNonPreparatorySets,
  countSetsOfType,
  isSyntheticPlanExerciseId,
  nextPhysicalSetNumber,
  parseNumericTarget,
  reconcileWarmupState,
} from '../workout-mode-types';
export type {
  AdvancedLocalStageProgress,
  MidExerciseExtra,
  PreparatorySetType,
  WarmupState,
  WorkoutPlanExercise,
} from '../workout-mode-types';

// Vocabulário de `set_type` (espelho de `api/app/set_types.py`).
export {
  WORKING_SET_TYPE,
  isPreparatorySetType,
  isTechniqueApplicationSetType,
  isWorkingSetType,
  normalizeSetType,
} from '../set-types';

// Catálogo de técnicas avançadas (fallback local; o cliente pode injetar o da API).
export {
  FALLBACK_ADVANCED_TECHNIQUES,
  getAdvancedTechniqueApplicationNoun,
  getAdvancedTechniqueCatalog,
  getAdvancedTechniqueDefaultSetsCount,
  getAdvancedTechniqueDescription,
  getAdvancedTechniqueLabel,
  getAdvancedTechniqueSetType,
  isApplicationStageTechnique,
  isExtraRepsTechnique,
  isSetsOnlyTechnique,
  resetAdvancedTechniqueCatalog,
  setAdvancedTechniqueCatalog,
} from '../advanced-techniques';
export type { AdvancedTechniqueCatalogItem } from '../advanced-techniques';

// Defaults de prescrição (dono: `api/app/domain/prescription_defaults.py`).
export {
  DEFAULT_MAX_REST_SECONDS,
  DEFAULT_MIN_REST_SECONDS,
  DEFAULT_REPS_TARGET,
  DEFAULT_REST_SECONDS,
  DEFAULT_RPE_TARGET,
  MIN_SETS_COUNT,
  REST_SECONDS_SANITY_MAX,
  REST_SECONDS_SANITY_MIN,
  SET_LOGGER_FALLBACK_LOAD_KG,
  SET_LOGGER_FALLBACK_REPS,
} from '../prescription-defaults';

// Constantes geradas pelo backend, como vieram (não edite: regenere no dono).
export { default as MOTOR_CONSTANTS } from '../motor-constants.generated.json';
export { default as SET_TYPES_VOCABULARY } from '../set-types.generated.json';

// Fila com argumentos só JSON (listas de ids no lugar de `Set`/callback).
export {
  buildExecutionQueueFromIds,
  findPlanItemForSubstitutedExerciseFromIds,
  findPlanItemsCoveredByAnotherSlotIds,
  reorderPendingBySkipByPlanItem,
} from './json-api';
