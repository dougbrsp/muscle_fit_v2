// Contadores de um item da ficha no Modo Treino (PR B2 da Evolução ZEPP).
//
// Montagem que antes morava inline em `modo-treino/PlanExecutionScreen.tsx`
// (e que o relógio teve de copiar): alvo de séries de trabalho, sub-etapas de
// aquecimento/ajuste do treino avançado e aplicações da técnica. Função PURA
// (sem React/DOM), exportada pelo núcleo `workout-mode-core`.
//
// Nenhum número à mão: o piso de séries vem do dono
// `api/app/domain/prescription_defaults.py` (`MIN_SETS_COUNT`, espelho
// `motor-constants.generated.json` via `prescription-defaults.ts`); a contagem
// de aplicações é de `techniqueStageTarget` e a conclusão/sub-etapa de
// `workout-mode-plan-flow.ts` (`isAdvancedExerciseDone`/`resolveAdvancedSubStage`).

import { isExtraRepsTechnique } from './advanced-techniques';
import { MIN_SETS_COUNT } from './prescription-defaults';
import { effectiveAdvancedStageDone } from './workout-mode-advanced-stage';
import type { AdvancedExerciseCounters } from './workout-mode-plan-flow';
import { techniqueStageTarget } from './workout-mode-technique';
import { countNonPreparatorySets, type AdvancedLocalStageProgress } from './workout-mode-types';
import type { WorkoutPlanExercise } from './workout-mode-types';

export type AdvancedCountersPlanExercise = Pick<
  WorkoutPlanExercise,
  | 'sets_count'
  | 'warmup_reps'
  | 'warmup_sets_count'
  | 'feeler_set_reps'
  | 'feeler_set_sets_count'
  | 'advanced_technique'
  | 'advanced_technique_applications'
>;

/**
 * Aplicações da técnica que viram etapa DEPOIS das séries de trabalho: as de
 * rest-pause/drop (`techniqueStageTarget`) e, na família "além da falha", as
 * `advanced_technique_applications` da prescrição (uma etapa por aplicação,
 * gravada como linha `forced_rep` ligada à última série). Sem aplicações
 * prescritas a família segue como "reps extras" na última série. Quem decide a
 * contagem é a prescrição; aqui só se lê.
 */
function techniqueApplicationsTarget(planExercise: AdvancedCountersPlanExercise): number {
  const stageTarget = techniqueStageTarget(planExercise);
  if (stageTarget > 0 || !isExtraRepsTechnique(planExercise.advanced_technique)) return stageTarget;
  return Math.max(0, planExercise.advanced_technique_applications || 0);
}

/**
 * Contadores do item (`AdvancedExerciseCounters`), a mesma base para `done`
 * (`isAdvancedExerciseDone`) e para a sub-etapa exibida (`resolveAdvancedSubStage`).
 *
 * - `workingTarget`: séries sugeridas para o par trocado (`adjustedSetsCount`,
 *   da API) > `sets_count` da ficha > piso `MIN_SETS_COUNT`.
 * - Aquecimento/ajuste só existem com `advancedMode` e com as reps configuradas;
 *   sem contagem (nula ou 0), vale o piso `MIN_SETS_COUNT`. Fora do avançado os
 *   alvos são 0 e só `techniqueSkipped` importa.
 * - `*Done` de aquecimento/ajuste = o MAIOR entre o contador local (que inclui
 *   as puladas) e o gravado no backend (idempotência ao retomar sem o estado
 *   local), limitado ao alvo.
 * - `loggedNonPreparatory` não conta aquecimento/ajuste ("Série N de S").
 */
export function buildAdvancedExerciseCounters(params: {
  planExercise: AdvancedCountersPlanExercise;
  loggedSets: ReadonlyArray<{ set_type?: string | null }>;
  advancedMode: boolean;
  adjustedSetsCount?: number | null;
  localProgress?: Partial<AdvancedLocalStageProgress> | null;
}): AdvancedExerciseCounters {
  const { planExercise, loggedSets, advancedMode, adjustedSetsCount, localProgress } = params;
  const workingTarget = adjustedSetsCount ?? planExercise.sets_count ?? MIN_SETS_COUNT;
  const warmupTarget =
    advancedMode && planExercise.warmup_reps != null ? planExercise.warmup_sets_count || MIN_SETS_COUNT : 0;
  const feelerTarget =
    advancedMode && planExercise.feeler_set_reps != null ? planExercise.feeler_set_sets_count || MIN_SETS_COUNT : 0;
  // Progresso efetivo (dono: `effectiveAdvancedStageDone`), limitado ao alvo.
  const effective = effectiveAdvancedStageDone({ localProgress, loggedSets });
  const warmupDone = Math.min(effective.warmupDone, warmupTarget);
  const feelerDone = Math.min(effective.feelerDone, feelerTarget);
  return {
    workingTarget,
    loggedNonPreparatory: countNonPreparatorySets(loggedSets),
    warmupTarget,
    warmupDone,
    feelerTarget,
    feelerDone,
    techniqueTarget: techniqueApplicationsTarget(planExercise),
    techniqueSkipped: localProgress?.techniqueSkipped ?? 0,
  };
}
