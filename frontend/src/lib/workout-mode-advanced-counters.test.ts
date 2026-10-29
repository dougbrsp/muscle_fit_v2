// PR B2 da Evolução ZEPP: `buildAdvancedExerciseCounters` substitui a montagem
// inline de `PlanExecutionScreen.tsx`. Prova de "zero mudança de comportamento":
// a fórmula ANTIGA da tela (copiada aqui como referência, com os literais que
// ela tinha) e a função do núcleo dão o mesmo resultado numa grade de entradas.

import { describe, expect, it } from 'vitest';

import { buildAdvancedExerciseCounters, type AdvancedCountersPlanExercise } from './workout-mode-advanced-counters';
import type { AdvancedExerciseCounters } from './workout-mode-plan-flow';
import { techniqueStageTarget } from './workout-mode-technique';
import { countNonPreparatorySets, countSetsOfType, type AdvancedLocalStageProgress } from './workout-mode-types';

type Sets = Array<{ set_type?: string | null }>;

// Cópia literal da tela antes do PR B2 (develop 2ccec59, linhas ~542-589).
function legacyCounters(
  planExercise: AdvancedCountersPlanExercise,
  loggedSets: Sets,
  advancedMode: boolean,
  adjusted: number | undefined,
  localAdvanced: AdvancedLocalStageProgress | undefined,
): AdvancedExerciseCounters {
  const loggedCount = countNonPreparatorySets(loggedSets);
  const workingSetsTarget = adjusted ?? planExercise.sets_count ?? 1;
  const warmupTarget = advancedMode && planExercise.warmup_reps != null ? planExercise.warmup_sets_count || 1 : 0;
  const feelerTarget = advancedMode && planExercise.feeler_set_reps != null ? planExercise.feeler_set_sets_count || 1 : 0;
  const techniqueTarget = techniqueStageTarget(planExercise);
  const warmupDone = Math.min(Math.max(localAdvanced?.warmupDone ?? 0, countSetsOfType(loggedSets, 'warmup')), warmupTarget);
  const feelerDone = Math.min(Math.max(localAdvanced?.feelerDone ?? 0, countSetsOfType(loggedSets, 'feeler')), feelerTarget);
  return {
    workingTarget: workingSetsTarget,
    loggedNonPreparatory: loggedCount,
    warmupTarget,
    warmupDone,
    feelerTarget,
    feelerDone,
    techniqueTarget,
    techniqueSkipped: localAdvanced?.techniqueSkipped ?? 0,
  };
}

const PLAN_EXERCISES: AdvancedCountersPlanExercise[] = [
  {},
  { sets_count: 3 },
  { sets_count: 0 },
  { sets_count: null, warmup_reps: 12, warmup_sets_count: 0, feeler_set_reps: 5, feeler_set_sets_count: null },
  { sets_count: 4, warmup_reps: 12, warmup_sets_count: 2, feeler_set_reps: 5, feeler_set_sets_count: 1 },
  { sets_count: 3, advanced_technique: 'rest_pause', advanced_technique_applications: 3 },
  { sets_count: 3, advanced_technique: 'drop_set', advanced_technique_applications: null },
  { sets_count: 3, advanced_technique: 'fst7', advanced_technique_applications: 7, warmup_reps: 10 },
];
const LOGGED: Sets[] = [
  [],
  [{ set_type: 'warmup' }, { set_type: 'feeler' }],
  [{ set_type: 'warmup' }, { set_type: 'warmup' }, { set_type: 'warmup' }, { set_type: null }, { set_type: 'working' }],
  [{ set_type: 'working' }, { set_type: 'working' }, { set_type: 'working' }, { set_type: 'drop_set' }, { set_type: 'rest_pause' }],
];
const LOCAL: Array<AdvancedLocalStageProgress | undefined> = [
  undefined,
  { warmupDone: 0, feelerDone: 0, techniqueSkipped: 0 },
  { warmupDone: 5, feelerDone: 1, techniqueSkipped: 2 },
];

describe('buildAdvancedExerciseCounters = montagem antiga da tela', () => {
  it('mesma saída em toda a grade (ficha × gravadas × modo × ajuste × progresso local)', () => {
    let checked = 0;
    for (const planExercise of PLAN_EXERCISES) {
      for (const loggedSets of LOGGED) {
        for (const advancedMode of [false, true]) {
          for (const adjusted of [undefined, 2, 5]) {
            for (const localProgress of LOCAL) {
              expect(
                buildAdvancedExerciseCounters({ planExercise, loggedSets, advancedMode, adjustedSetsCount: adjusted, localProgress }),
              ).toEqual(legacyCounters(planExercise, loggedSets, advancedMode, adjusted, localProgress));
              checked += 1;
            }
          }
        }
      }
    }
    expect(checked).toBe(PLAN_EXERCISES.length * LOGGED.length * 2 * 3 * LOCAL.length);
  });

  it('`null` no ajuste e no progresso local (estado JSON do relógio) = ausente', () => {
    const planExercise = PLAN_EXERCISES[4];
    const loggedSets = LOGGED[2];
    expect(
      buildAdvancedExerciseCounters({ planExercise, loggedSets, advancedMode: true, adjustedSetsCount: null, localProgress: null }),
    ).toEqual(legacyCounters(planExercise, loggedSets, true, undefined, undefined));
  });
});
