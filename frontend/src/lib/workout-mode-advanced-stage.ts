// Sub-etapa do treino avançado e progresso LOCAL das sub-etapas do Modo
// Treino (PR B3 da Evolução ZEPP).
//
// Montagem que antes morava inline em `modo-treino/PlanExecutionScreen.tsx`
// (e que o relógio teve de copiar): se o item tem fluxo avançado, qual
// sub-etapa mostrar e como o contador local (`AdvancedLocalStageProgress`)
// avança quando o usuário conclui/pula aquecimento, ajuste ou uma aplicação da
// técnica. Funções PURAS (sem React/DOM), só com argumentos JSON, exportadas
// pelo núcleo `workout-mode-core`.
//
// Nenhum número de regra aqui: a sub-etapa é de `resolveAdvancedSubStage`
// (`workout-mode-plan-flow.ts`), os alvos de `buildAdvancedExerciseCounters`
// (piso `MIN_SETS_COUNT`, dono `api/app/domain/prescription_defaults.py`) e a
// contagem do gravado de `countSetsOfType`. O `+ count` é o próprio evento
// (uma série concluída/pulada, ou N aplicações encerradas de uma vez).

import { resolveAdvancedSubStage, type AdvancedExerciseCounters, type AdvancedSubStage } from './workout-mode-plan-flow';
import { isRepBased } from './set-measures';
import { countSetsOfType, type AdvancedLocalStageProgress, type WorkoutPlanExercise } from './workout-mode-types';

export type AdvancedFlowPlanExercise = Pick<WorkoutPlanExercise, 'advanced_technique' | 'warmup_reps' | 'feeler_set_reps'> &
  Partial<Pick<WorkoutPlanExercise, 'measure'>>;

/** Sub-etapas cujo progresso local avança ao concluir/pular. */
export type AdvancedLocalStageKind = 'warmup' | 'feeler' | 'technique';

/**
 * O item configura alguma etapa do treino avançado: técnica (`advanced_technique`
 * não-nulo), aquecimento (`warmup_reps` não-nulo) ou série de ajuste
 * (`feeler_set_reps` não-nulo) — os mesmos campos que montam as sub-etapas em
 * `buildAdvancedExerciseCounters`/`techniqueStageTarget`. Só o código da
 * técnica conta: quando a segurança remove a técnica na geração, a ficha fica
 * com `advanced_technique` NULL e `advanced_technique_applications`/
 * `duration_seconds` como resíduo, e esse resíduo NÃO torna o item avançado
 * (`techniqueStageTarget` também o ignora). Pico de contração sozinho não
 * abre sub-etapa, então não conta. Sem item: `false`.
 *
 * Medidas de série (Lote T5): o bloco avançado só existe em item de
 * repetições (`isRepBased`, espelho do dono `set_measures.py`; o servidor já
 * recusa técnica/aquecimento/ajuste em item por tempo ou máximo de reps). Um
 * item fora de `reps` nunca é avançado, nem com resíduo desses campos.
 */
export function exerciseHasAdvancedConfig(planExercise: AdvancedFlowPlanExercise | null | undefined): boolean {
  return Boolean(
    planExercise &&
      isRepBased(planExercise.measure) &&
      (planExercise.advanced_technique != null || planExercise.warmup_reps != null || planExercise.feeler_set_reps != null),
  );
}

/**
 * O item tem fluxo avançado: modo avançado ligado E o item configura técnica,
 * aquecimento ou ajuste (`exerciseHasAdvancedConfig`). Aquecimento/ajuste são
 * independentes da técnica (um item só com aquecimento ainda passa por essas
 * sub-etapas). Sem item: `false`.
 */
export function hasAdvancedFlow(params: {
  advancedMode: boolean;
  planExercise: AdvancedFlowPlanExercise | null | undefined;
}): boolean {
  return Boolean(params.advancedMode && exerciseHasAdvancedConfig(params.planExercise));
}

/** Modo de execução de uma ficha no Modo Treino (celular e relógio). */
export type WorkoutMode = 'advanced' | 'normal';

/** A ficha tem pelo menos um item com configuração avançada (`exerciseHasAdvancedConfig`). */
export function planHasAdvancedConfig(
  planExercises: ReadonlyArray<AdvancedFlowPlanExercise | null | undefined> | null | undefined,
): boolean {
  return (planExercises ?? []).some((planExercise) => exerciseHasAdvancedConfig(planExercise));
}

/**
 * Modo do treino decidido pela FICHA (PR B4 da Evolução ZEPP, decisão do
 * usuário de 2026-10-19): sem escolha "normal/avançado" antes de começar. Se
 * algum item configura aquecimento, série de ajuste ou técnica avançada, o
 * treino é `'advanced'`; senão `'normal'`. Ficha vazia/ausente: `'normal'`.
 * Uma regra só para o celular e o relógio. Quem chama decide UMA vez, ao
 * iniciar a sessão, e grava o resultado (o modo não muda no meio do treino).
 */
export function resolveWorkoutModeFromPlan(
  planExercises: ReadonlyArray<AdvancedFlowPlanExercise | null | undefined> | null | undefined,
): WorkoutMode {
  return planHasAdvancedConfig(planExercises) ? 'advanced' : 'normal';
}

/**
 * Sub-etapa exibida do item (`resolveAdvancedSubStage`) ou `null` (série de
 * trabalho comum). Vale com fluxo avançado (`hasAdvancedFlow`) ou, nos dois
 * modos (Fase 5), quando o item tem aplicações de técnica como sub-etapa
 * (`counters.techniqueTarget > 0`). Sem item/contadores: `null`.
 */
export function advancedStageOf(params: {
  advancedMode: boolean;
  planExercise: AdvancedFlowPlanExercise | null | undefined;
  counters: AdvancedExerciseCounters | null | undefined;
}): AdvancedSubStage | null {
  const { counters } = params;
  if (!params.planExercise || !counters) return null;
  const hasTechniqueStage = counters.techniqueTarget > 0;
  return hasAdvancedFlow(params) || hasTechniqueStage ? resolveAdvancedSubStage(counters) : null;
}

/**
 * Progresso EFETIVO de aquecimento/ajuste: o MAIOR entre o contador local (que
 * inclui as puladas) e o que já está gravado no backend. Sem limite pelo alvo
 * (quem limita é `buildAdvancedExerciseCounters`): é a base do incremento.
 */
export function effectiveAdvancedStageDone(params: {
  localProgress?: Partial<AdvancedLocalStageProgress> | null;
  loggedSets: ReadonlyArray<{ set_type?: string | null }>;
}): { warmupDone: number; feelerDone: number } {
  const { localProgress, loggedSets } = params;
  return {
    warmupDone: Math.max(localProgress?.warmupDone ?? 0, countSetsOfType(loggedSets, 'warmup')),
    feelerDone: Math.max(localProgress?.feelerDone ?? 0, countSetsOfType(loggedSets, 'feeler')),
  };
}

/**
 * Aplicações da técnica que "Encerrar técnica" marca de uma vez como puladas:
 * a da sub-etapa atual e as seguintes (`total - index + 1`, com `index`
 * 1-based de `resolveAdvancedSubStage`). É o `count` de
 * `advanceLocalStageProgress({kind: 'technique'})`. Sem piso: a sub-etapa
 * sempre tem `index <= total`.
 */
export function remainingTechniqueApplications(stage: Pick<AdvancedSubStage, 'index' | 'total'>): number {
  return stage.total - stage.index + 1;
}

/**
 * Novo progresso local do item depois de concluir/pular uma sub-etapa.
 *
 * - `warmup`/`feeler`: parte do progresso EFETIVO capturado no clique
 *   (`effectiveDone`, de `effectiveAdvancedStageDone`) e do local atual, o
 *   maior dos dois, + 1 — não duplica a contagem quando o refetch já trouxe a
 *   série recém-gravada.
 * - `technique`: só soma `count` às aplicações puladas (`count` > 1 só para
 *   "Encerrar técnica", que marca de uma vez as restantes).
 * - Os outros contadores ficam como estavam. Sem progresso local: parte de zero.
 */
export function advanceLocalStageProgress(params: {
  existing?: AdvancedLocalStageProgress | null;
  kind: AdvancedLocalStageKind;
  effectiveDone: { warmupDone: number; feelerDone: number };
  count?: number;
}): AdvancedLocalStageProgress {
  const { kind, effectiveDone, count = 1 } = params;
  const existing = params.existing ?? { warmupDone: 0, feelerDone: 0, techniqueSkipped: 0 };
  return {
    warmupDone: kind === 'warmup' ? Math.max(existing.warmupDone, effectiveDone.warmupDone) + 1 : existing.warmupDone,
    feelerDone: kind === 'feeler' ? Math.max(existing.feelerDone, effectiveDone.feelerDone) + 1 : existing.feelerDone,
    techniqueSkipped: kind === 'technique' ? existing.techniqueSkipped + count : existing.techniqueSkipped,
  };
}
