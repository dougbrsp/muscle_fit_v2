// PR B3 da Evolução ZEPP: `hasAdvancedFlow`/`advancedStageOf`/
// `effectiveAdvancedStageDone`/`advanceLocalStageProgress` substituem a
// montagem inline de `PlanExecutionScreen.tsx`, o seletor da sessão anterior
// mudou de módulo e o piso de séries da continuação sintética virou
// `MIN_SETS_COUNT`. Prova de "zero mudança de comportamento": as fórmulas
// ANTIGAS da tela (copiadas aqui como referência, com os literais que tinham)
// e as funções do núcleo dão o mesmo resultado numa grade de entradas.

import { describe, expect, it } from 'vitest';

import { MIN_SETS_COUNT } from './prescription-defaults';
import {
  advanceLocalStageProgress,
  advancedStageOf,
  effectiveAdvancedStageDone,
  exerciseHasAdvancedConfig,
  hasAdvancedFlow,
  planHasAdvancedConfig,
  remainingTechniqueApplications,
  resolveWorkoutModeFromPlan,
  type AdvancedFlowPlanExercise,
  type AdvancedLocalStageKind,
} from './workout-mode-advanced-stage';
import {
  buildSyntheticContinuation,
  resolveAdvancedSubStage,
  type AdvancedExerciseCounters,
  type AdvancedSubStage,
} from './workout-mode-plan-flow';
import { selectPreviousSessionWorkingSets as fromPreviousSession } from './workout-mode-previous-session';
import { selectPreviousSessionWorkingSets } from './workout-mode-previous-session-sets';
import { countSetsOfType, type AdvancedLocalStageProgress, type WorkoutPlanExercise } from './workout-mode-types';

type Sets = Array<{ set_type?: string | null }>;
type Current = { planExercise: AdvancedFlowPlanExercise; advancedCounters: AdvancedExerciseCounters } | null;

// --- Cópia literal da tela antes do PR B3 (develop ecd345d, linhas ~896-947) ---

function legacyStage(advancedMode: boolean, current: Current): { flow: boolean; stage: AdvancedSubStage | null } {
  const hasAdvancedFlowOld = Boolean(
    advancedMode &&
      current &&
      (current.planExercise.advanced_technique != null ||
        current.planExercise.warmup_reps != null ||
        current.planExercise.feeler_set_reps != null),
  );
  const hasTechniqueStage = Boolean(current && current.advancedCounters.techniqueTarget > 0);
  const advancedStage: AdvancedSubStage | null =
    (hasAdvancedFlowOld || hasTechniqueStage) && current ? resolveAdvancedSubStage(current.advancedCounters) : null;
  return { flow: hasAdvancedFlowOld, stage: advancedStage };
}

function legacyEffective(currentAdvancedProgress: AdvancedLocalStageProgress | undefined, advancedLoggedSets: Sets) {
  const advancedWarmupDoneCount = Math.max(
    currentAdvancedProgress?.warmupDone ?? 0,
    countSetsOfType(advancedLoggedSets, 'warmup'),
  );
  const advancedFeelerDoneCount = Math.max(
    currentAdvancedProgress?.feelerDone ?? 0,
    countSetsOfType(advancedLoggedSets, 'feeler'),
  );
  return { warmupDone: advancedWarmupDoneCount, feelerDone: advancedFeelerDoneCount };
}

function legacyAdvance(
  curr: Record<number, AdvancedLocalStageProgress>,
  currentWorkoutPlanExerciseId: number,
  kind: AdvancedLocalStageKind,
  effectiveWarmupDone: number,
  effectiveFeelerDone: number,
  count = 1,
): AdvancedLocalStageProgress {
  const existing = curr[currentWorkoutPlanExerciseId] ?? { warmupDone: 0, feelerDone: 0, techniqueSkipped: 0 };
  return {
    warmupDone: kind === 'warmup' ? Math.max(existing.warmupDone, effectiveWarmupDone) + 1 : existing.warmupDone,
    feelerDone: kind === 'feeler' ? Math.max(existing.feelerDone, effectiveFeelerDone) + 1 : existing.feelerDone,
    techniqueSkipped: kind === 'technique' ? existing.techniqueSkipped + count : existing.techniqueSkipped,
  };
}

// --- Grade de entradas --------------------------------------------------------

const PLAN_EXERCISES: AdvancedFlowPlanExercise[] = [
  {},
  { advanced_technique: null, warmup_reps: null, feeler_set_reps: null },
  { warmup_reps: 12 },
  { feeler_set_reps: 5 },
  { warmup_reps: 0 },
  { advanced_technique: 'rest_pause' },
  { advanced_technique: 'fst7', warmup_reps: 10, feeler_set_reps: 4 },
];

function counters(overrides: Partial<AdvancedExerciseCounters>): AdvancedExerciseCounters {
  return {
    workingTarget: 3,
    loggedNonPreparatory: 0,
    warmupTarget: 0,
    warmupDone: 0,
    feelerTarget: 0,
    feelerDone: 0,
    techniqueTarget: 0,
    techniqueSkipped: 0,
    ...overrides,
  };
}

const COUNTERS: AdvancedExerciseCounters[] = [
  counters({}),
  counters({ warmupTarget: 2, warmupDone: 1 }),
  counters({ warmupTarget: 2, warmupDone: 2, feelerTarget: 1 }),
  counters({ loggedNonPreparatory: 3, techniqueTarget: 3 }),
  counters({ loggedNonPreparatory: 4, techniqueTarget: 3, techniqueSkipped: 1 }),
  counters({ loggedNonPreparatory: 6, techniqueTarget: 3 }),
  counters({ loggedNonPreparatory: 1, techniqueTarget: 2 }),
];

const LOGGED: Sets[] = [
  [],
  [{ set_type: 'warmup' }, { set_type: 'feeler' }],
  [{ set_type: 'Warm-up' }, { set_type: 'warmup' }, { set_type: 'warmup' }, { set_type: null }, { set_type: 'working' }],
  [{ set_type: 'feeler' }, { set_type: 'feeler' }, { set_type: 'drop_set' }],
];

const LOCAL: Array<AdvancedLocalStageProgress | undefined> = [
  undefined,
  { warmupDone: 0, feelerDone: 0, techniqueSkipped: 0 },
  { warmupDone: 3, feelerDone: 1, techniqueSkipped: 2 },
  { warmupDone: 1, feelerDone: 4, techniqueSkipped: 0 },
];

const KINDS: AdvancedLocalStageKind[] = ['warmup', 'feeler', 'technique'];

describe('hasAdvancedFlow / advancedStageOf (equivalência com a tela antiga)', () => {
  it('mesmo resultado em toda a grade, com e sem item corrente', () => {
    let checked = 0;
    for (const advancedMode of [false, true]) {
      const none = legacyStage(advancedMode, null);
      expect(hasAdvancedFlow({ advancedMode, planExercise: undefined })).toBe(none.flow);
      expect(advancedStageOf({ advancedMode, planExercise: undefined, counters: undefined })).toEqual(none.stage);
      for (const planExercise of PLAN_EXERCISES) {
        for (const advancedCounters of COUNTERS) {
          const old = legacyStage(advancedMode, { planExercise, advancedCounters });
          expect(hasAdvancedFlow({ advancedMode, planExercise })).toBe(old.flow);
          expect(advancedStageOf({ advancedMode, planExercise, counters: advancedCounters })).toEqual(old.stage);
          checked += 1;
        }
      }
    }
    expect(checked).toBe(2 * PLAN_EXERCISES.length * COUNTERS.length);
  });

  it('item presente sem contadores: null (contrato do núcleo; a tela sempre traz os contadores)', () => {
    for (const advancedMode of [false, true]) {
      for (const planExercise of PLAN_EXERCISES) {
        expect(advancedStageOf({ advancedMode, planExercise, counters: null })).toBeNull();
      }
    }
  });
});

describe('remainingTechniqueApplications (equivalência com "Encerrar técnica" da tela antiga)', () => {
  it('igual a `advancedStage.total - advancedStage.index + 1` em toda sub-etapa de técnica possível', () => {
    for (let total = 1; total <= 8; total += 1) {
      for (let index = 1; index <= total; index += 1) {
        const advancedStage: AdvancedSubStage = { kind: 'technique', index, total };
        expect(remainingTechniqueApplications(advancedStage)).toBe(advancedStage.total - advancedStage.index + 1);
      }
    }
  });
});

describe('effectiveAdvancedStageDone / advanceLocalStageProgress (equivalência com a tela antiga)', () => {
  it('progresso efetivo igual ao Math.max inline', () => {
    for (const local of LOCAL) {
      for (const logged of LOGGED) {
        expect(effectiveAdvancedStageDone({ localProgress: local, loggedSets: logged })).toEqual(legacyEffective(local, logged));
      }
    }
  });

  it('incremento igual ao updater de setAdvancedProgress', () => {
    const id = 14;
    for (const existing of LOCAL) {
      for (const captured of LOCAL) {
        for (const logged of LOGGED) {
          const effective = legacyEffective(captured, logged);
          for (const kind of KINDS) {
            for (const count of [undefined, 1, 3]) {
              const curr: Record<number, AdvancedLocalStageProgress> = existing ? { [id]: existing } : {};
              const old =
                count === undefined
                  ? legacyAdvance(curr, id, kind, effective.warmupDone, effective.feelerDone)
                  : legacyAdvance(curr, id, kind, effective.warmupDone, effective.feelerDone, count);
              const params = { existing: curr[id], kind, effectiveDone: effective };
              const next = advanceLocalStageProgress(count === undefined ? params : { ...params, count });
              expect(next).toEqual(old);
            }
          }
        }
      }
    }
  });
});

describe('piso de séries da continuação sintética (MIN_SETS_COUNT)', () => {
  const current: WorkoutPlanExercise = {
    workout_plan_exercise_id: 14,
    sequence: 1,
    exercise_name: 'Remada baixa',
    mapping_id: 104,
    sets_count: 3,
    rest_seconds: 90,
  };
  const choice = { exercise_name: 'Remada máquina', mapping_id: 105, equipment_link_id: 7, equipment_name: 'Máquina' };
  const targets = { reps_target: null, load_target: null, rpe_target: null };

  it('o espelho vale o 1 que a tela e o fluxo escreviam à mão', () => {
    expect(MIN_SETS_COUNT).toBe(1);
  });

  it.each([-2, -1, 0, 1, 2, 5])('faltando %i (alvo - registradas): igual ao Math.max(1, Math.max(1, x)) antigo', (raw) => {
    const extra = buildSyntheticContinuation({ localId: -1, current, remainingSets: raw, choice, targets, existingExtras: [] });
    // Antes: a tela passava `Math.max(1, alvo - registradas)` e o fluxo aplicava `Math.max(1, remainingSets)`.
    expect(extra.sets_count).toBe(Math.max(1, Math.max(1, raw)));
  });
});

describe('selectPreviousSessionWorkingSets (módulo puro do núcleo)', () => {
  it('é a MESMA função reexportada por workout-mode-previous-session', () => {
    expect(fromPreviousSession).toBe(selectPreviousSessionWorkingSets);
  });
});

describe('resolveWorkoutModeFromPlan (PR B4: modo decidido pela ficha)', () => {
  const values = {
    advanced_technique: [null, undefined, 'drop_set'],
    warmup_reps: [null, undefined, 0, 12],
    feeler_set_reps: [null, undefined, 6],
  } as const;
  const grid: AdvancedFlowPlanExercise[] = [];
  for (const advanced_technique of values.advanced_technique)
    for (const warmup_reps of values.warmup_reps)
      for (const feeler_set_reps of values.feeler_set_reps) grid.push({ advanced_technique, warmup_reps, feeler_set_reps });

  it('o item é avançado exatamente quando teria fluxo avançado no modo avançado', () => {
    for (const planExercise of grid) {
      expect(exerciseHasAdvancedConfig(planExercise)).toBe(hasAdvancedFlow({ advancedMode: true, planExercise }));
    }
  });

  it('a ficha é avançada se QUALQUER item tem configuração avançada', () => {
    const plain: AdvancedFlowPlanExercise = { advanced_technique: null, warmup_reps: null, feeler_set_reps: null };
    for (const planExercise of grid) {
      const expected = exerciseHasAdvancedConfig(planExercise) ? 'advanced' : 'normal';
      expect(resolveWorkoutModeFromPlan([plain, planExercise, plain])).toBe(expected);
      expect(planHasAdvancedConfig([plain, planExercise])).toBe(expected === 'advanced');
    }
  });

  it('resíduo de técnica removida pela segurança não torna o treino avançado', () => {
    const residue = {
      advanced_technique: null,
      advanced_technique_applications: 3,
      advanced_technique_duration_seconds: 15,
      warmup_reps: null,
      feeler_set_reps: null,
    };
    expect(resolveWorkoutModeFromPlan([residue])).toBe('normal');
  });

  it('ficha vazia ou ausente é normal', () => {
    expect(resolveWorkoutModeFromPlan([])).toBe('normal');
    expect(resolveWorkoutModeFromPlan(null)).toBe('normal');
    expect(resolveWorkoutModeFromPlan(undefined)).toBe('normal');
  });
});
