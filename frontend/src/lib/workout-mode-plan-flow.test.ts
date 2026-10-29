import { describe, expect, it } from 'vitest';

import {
  buildExecutionQueue,
  buildSyntheticContinuation,
  originalPlanItemId,
  executionPositionOrdinal,
  findPlanItemForSubstitutedExercise,
  findPlanItemsCoveredByAnotherSlot,
  findRemainingAfterUpcoming,
  findUpcomingExercise,
  isAdvancedExerciseDone,
  mergeResumedSession,
  progressAfterEquipmentSwap,
  reorderPendingBySkip,
  resolveAdvancedSubStage,
  resolveEffectiveExerciseDisplay,
  resolveRestSeconds,
  resolveSessionPairLimits,
  shouldSwapAsOverride,
  syntheticExtraToPlanExercise,
} from './workout-mode-plan-flow';
import type { AdvancedExerciseCounters } from './workout-mode-plan-flow';
import type {
  ExerciseLogRow,
  MidExerciseExtra,
  StoredWorkoutModeSession,
  WorkoutPlanExercise,
} from './workout-mode-types';

type Item = { key: string; done: boolean };
const keyOf = (item: Item) => item.key;

describe('findUpcomingExercise (bug 1: próximo exercício)', () => {
  it('fora da transição: devolve o pendente seguinte ao atual', () => {
    const items: Item[] = [
      { key: 'ex1', done: true },
      { key: 'ex2', done: false },
      { key: 'ex3', done: false },
    ];
    expect(findUpcomingExercise(items, keyOf, 'ex2')?.key).toBe('ex3');
  });

  it('na transição após a última série do ex1 (activity já refeita, ex2 virou o atual): próximo é ex2, não ex3', () => {
    // Estado real no momento em que o descanso de transição abre: a última
    // série do ex1 já foi gravada e refeita, então ex1 está `done` e o
    // `current` ao vivo é ex2. O cálculo antigo ("pendente depois do
    // current") devolvia ex3 — o 2º exercício parecia ser pulado.
    const items: Item[] = [
      { key: 'ex1', done: true },
      { key: 'ex2', done: false },
      { key: 'ex3', done: false },
    ];
    expect(findUpcomingExercise(items, keyOf, 'ex1')?.key).toBe('ex2');
  });

  it('na transição, se o exercício encerrado ainda não consta como concluído, pula ele e mostra o seguinte', () => {
    const items: Item[] = [
      { key: 'ex1', done: false },
      { key: 'ex2', done: false },
    ];
    expect(findUpcomingExercise(items, keyOf, 'ex1')?.key).toBe('ex2');
  });

  it('respeita a ordem efetiva (pulados no fim) e devolve null no último', () => {
    const items: Item[] = [
      { key: 'ex1', done: true },
      { key: 'ex3', done: false },
      { key: 'ex2', done: false },
    ];
    expect(findUpcomingExercise(items, keyOf, 'ex1')?.key).toBe('ex3');
    expect(findUpcomingExercise([{ key: 'ex1', done: false }], keyOf, 'ex1')).toBeNull();
  });
});

describe('findRemainingAfterUpcoming (M64: restantes na pausa de transição)', () => {
  const keysOf = (rows: { item: Item }[]) => rows.map((row) => row.item.key);

  it('lista os pendentes depois do próximo, na ordem, sem concluídos, sem o encerrado e sem o próximo', () => {
    const items: Item[] = [
      { key: 'ex1', done: true },
      { key: 'ex2', done: true },
      { key: 'ex3', done: false },
      { key: 'ex4', done: false },
      { key: 'ex5', done: false },
    ];
    // Transição após encerrar ex2: próximo = ex3 (card), restantes = ex4, ex5.
    expect(findUpcomingExercise(items, keyOf, 'ex2')?.key).toBe('ex3');
    const rows = findRemainingAfterUpcoming(items, keyOf, 'ex2');
    expect(keysOf(rows)).toEqual(['ex4', 'ex5']);
    expect(rows.map((row) => row.ordinal)).toEqual([4, 5]);
  });

  it('exclui o recém-encerrado mesmo se ele ainda não consta como concluído', () => {
    const items: Item[] = [
      { key: 'ex1', done: false },
      { key: 'ex2', done: false },
      { key: 'ex3', done: false },
    ];
    const rows = findRemainingAfterUpcoming(items, keyOf, 'ex1');
    expect(keysOf(rows)).toEqual(['ex3']);
    expect(rows[0].ordinal).toBe(3);
  });

  it('respeita a ordem efetiva: item pulado vai para o fim e item trocado (concluído + continuação) segue a fila', () => {
    // Ordem efetiva após `reorderPendingBySkip`: ex2 foi pulado (fim da fila);
    // ex3 foi trocado (item original coberto/concluído) e a continuação
    // sintética ex3-cont entra na fila.
    const items: Item[] = [
      { key: 'ex1', done: true },
      { key: 'ex3', done: true },
      { key: 'ex4', done: false },
      { key: 'ex3-cont', done: false },
      { key: 'ex5', done: false },
      { key: 'ex2', done: false },
    ];
    expect(findUpcomingExercise(items, keyOf, 'ex3')?.key).toBe('ex4');
    const rows = findRemainingAfterUpcoming(items, keyOf, 'ex3');
    expect(keysOf(rows)).toEqual(['ex3-cont', 'ex5', 'ex2']);
    expect(rows.map((row) => row.ordinal)).toEqual([4, 5, 6]);
  });

  it('vazio quando o próximo é o último ou não há próximo', () => {
    const lastTwo: Item[] = [
      { key: 'ex1', done: true },
      { key: 'ex2', done: false },
    ];
    expect(findRemainingAfterUpcoming(lastTwo, keyOf, 'ex1')).toEqual([]);
    expect(findRemainingAfterUpcoming([{ key: 'ex1', done: true }], keyOf, 'ex1')).toEqual([]);
    expect(findRemainingAfterUpcoming([], keyOf, null)).toEqual([]);
  });
});

const advancedPlanItem: WorkoutPlanExercise = {
  workout_plan_exercise_id: 42,
  sequence: 1,
  exercise_name: 'Supino reto',
  mapping_id: 7,
  muscle_primary: 'Peito',
  muscle_secondary: 'Tríceps',
  sets_count: 3,
  reps_target: '8-10',
  load_target: '60kg',
  rpe_target: '8',
  rest_seconds: 90,
  warmup_reps: 15,
  warmup_sets_count: 2,
  feeler_set_reps: 5,
  feeler_set_sets_count: 1,
  advanced_technique: 'drop_set',
  advanced_technique_applications: 1,
  peak_contraction_seconds: 2,
} as WorkoutPlanExercise;

const choice = { exercise_name: 'Supino máquina', mapping_id: 9, equipment_link_id: 99, equipment_name: 'Máquina X' };
const targets = { reps_target: '10-12', load_target: '50kg', rpe_target: '8' };

describe('troca de equipamento (bug 2: ajuste pulado)', () => {
  it('continuação sintética herda aquecimento/ajuste/técnica do item original', () => {
    const extra = buildSyntheticContinuation({
      localId: -1000,
      current: advancedPlanItem,
      remainingSets: 3,
      choice,
      targets,
      existingExtras: [],
    });
    const planExercise = syntheticExtraToPlanExercise(extra);
    expect(planExercise.workout_plan_exercise_id).toBe(-1000);
    expect(planExercise.feeler_set_reps).toBe(5);
    expect(planExercise.feeler_set_sets_count).toBe(1);
    expect(planExercise.warmup_reps).toBe(15);
    expect(planExercise.warmup_sets_count).toBe(2);
    expect(planExercise.advanced_technique).toBe('drop_set');
    expect(planExercise.advanced_technique_applications).toBe(1);
    expect(planExercise.peak_contraction_seconds).toBe(2);
    expect(planExercise.mapping_id).toBe(9);
    expect(planExercise.sets_count).toBe(3);
    expect(extra.originalWorkoutPlanExerciseId).toBe(42);
  });

  it('snapshot antigo de continuação (sem campos avançados) continua válido', () => {
    const legacy: MidExerciseExtra = {
      localId: -1000,
      exercise_name: 'X',
      mapping_id: 1,
      equipment_link_id: 2,
      equipment_name: 'Y',
      sets_count: 2,
      originalWorkoutPlanExerciseId: 5,
    };
    const planExercise = syntheticExtraToPlanExercise(legacy);
    expect(planExercise.feeler_set_reps).toBeUndefined();
    expect(planExercise.sets_count).toBe(2);
  });

  it('trocar de novo a partir de uma continuação mantém o vínculo com o item original da ficha', () => {
    const first = buildSyntheticContinuation({
      localId: -1000,
      current: advancedPlanItem,
      remainingSets: 2,
      choice,
      targets,
      existingExtras: [],
    });
    const second = buildSyntheticContinuation({
      localId: -1001,
      current: syntheticExtraToPlanExercise(first),
      remainingSets: 2,
      choice,
      targets,
      existingExtras: [first],
    });
    expect(second.originalWorkoutPlanExerciseId).toBe(42);
    expect(second.feeler_set_reps).toBe(5);
  });

  it('sem nada gravado (aquecimento/ajuste ainda pendentes) a troca vira override no próprio item', () => {
    expect(shouldSwapAsOverride({ hasLoggedRow: false, workoutPlanExerciseId: 42 })).toBe(true);
    expect(shouldSwapAsOverride({ hasLoggedRow: true, workoutPlanExerciseId: 42 })).toBe(false);
    expect(shouldSwapAsOverride({ hasLoggedRow: false, workoutPlanExerciseId: -1000 })).toBe(false);
    expect(shouldSwapAsOverride({ hasLoggedRow: false, workoutPlanExerciseId: null })).toBe(false);
  });

  it('após a troca o ajuste volta a ser oferecido e o aquecimento já feito é mantido', () => {
    expect(progressAfterEquipmentSwap({ warmupDone: 2 })).toEqual({ warmupDone: 2, feelerDone: 0, techniqueSkipped: 0 });
  });
});

describe('mergeResumedSession (bug 3: retomada perde aquecimento/ajuste)', () => {
  const base: StoredWorkoutModeSession = {
    session_id: 10,
    source: 'plan',
    workout_plan_id: 3,
    gym_id: 1,
    conducted_by: null,
  };
  const snapshot: StoredWorkoutModeSession = {
    session_id: 10,
    source: 'plan',
    workout_plan_id: 3,
    gym_id: 2,
    advanced_mode: true,
    advanced_stage_progress: { 42: { warmupDone: 1, feelerDone: 0, techniqueSkipped: 0 } },
    warmup_state: { 43: { first: 'skipped', second: 'pending' } },
    skipped_exercise_ids: [44],
    equipment_overrides: { 45: { mapping_id: 9, equipment_link_id: 99, exercise_name: 'A', equipment_name: 'B' } },
  };

  it('restaura modo avançado e progresso local da mesma sessão; campos do servidor prevalecem', () => {
    const merged = mergeResumedSession(base, snapshot);
    expect(merged.advanced_mode).toBe(true);
    expect(merged.advanced_stage_progress).toEqual(snapshot.advanced_stage_progress);
    expect(merged.warmup_state).toEqual(snapshot.warmup_state);
    expect(merged.skipped_exercise_ids).toEqual([44]);
    expect(merged.equipment_overrides).toEqual(snapshot.equipment_overrides);
    expect(merged.gym_id).toBe(1);
  });

  it('ignora snapshot de outra sessão ou ausente', () => {
    expect(mergeResumedSession(base, { ...snapshot, session_id: 11 })).toEqual(base);
    expect(mergeResumedSession(base, null)).toEqual(base);
  });
});

// Cenário sintético baseado em caso real (2026-09-17).
describe('buildExecutionQueue (item sem configuração avançada sumia no treino avançado)', () => {
  const item = (id: number, sequence: number, extra: Partial<WorkoutPlanExercise> = {}) =>
    ({ workout_plan_exercise_id: id, sequence, exercise_name: `ex${id}`, ...extra }) as WorkoutPlanExercise;

  it('mantém o exercício sem aquecimento/ajuste/técnica na fila', () => {
    const planExercises = [
      item(884, 7, { advanced_technique: 'rest_pause', feeler_set_reps: 6 }),
      item(885, 8), // extensão de tronco no banco romano, sem configuração avançada
    ];
    const queue = buildExecutionQueue({ planExercises, syntheticExercises: [], cancelledIds: new Set() });
    expect(queue.map((ex) => ex.workout_plan_exercise_id)).toEqual([884, 885]);
  });

  it('remove cancelados e ordena continuações sintéticas depois dos itens da ficha', () => {
    const queue = buildExecutionQueue({
      planExercises: [item(2, 2), item(1, 1)],
      syntheticExercises: [item(-1000, 9999)],
      cancelledIds: new Set([2]),
    });
    expect(queue.map((ex) => ex.workout_plan_exercise_id)).toEqual([1, -1000]);
  });
});

describe('resolveRestSeconds (rest-pause com 10 s entre mini-blocos)', () => {
  const base = { techniqueDurationSeconds: 10, restSeconds: 60, defaultRestSeconds: 60 };

  it('antes de um mini-bloco da técnica usa a duração configurada', () => {
    expect(resolveRestSeconds({ ...base, nextStageKind: 'technique', isExerciseTransition: false })).toBe(10);
  });

  it('fora da técnica usa o descanso da série', () => {
    expect(resolveRestSeconds({ ...base, nextStageKind: null, isExerciseTransition: false })).toBe(60);
  });

  it('na transição para o próximo exercício usa o descanso da série', () => {
    expect(resolveRestSeconds({ ...base, nextStageKind: 'technique', isExerciseTransition: true })).toBe(60);
  });

  it('técnica sem duração configurada (ex.: drop-set) mantém o descanso da série', () => {
    expect(
      resolveRestSeconds({ ...base, techniqueDurationSeconds: null, nextStageKind: 'technique', isExerciseTransition: false }),
    ).toBe(60);
  });

  it('sem descanso na ficha cai no padrão', () => {
    expect(
      resolveRestSeconds({ nextStageKind: null, isExerciseTransition: false, restSeconds: null, defaultRestSeconds: 60 }),
    ).toBe(60);
  });
});

describe('resolveRestSeconds (intervalo próprio de aquecimento e ajuste)', () => {
  const base = {
    nextStageKind: null,
    isExerciseTransition: false,
    warmupRestSeconds: 30,
    feelerRestSeconds: 90,
    techniqueDurationSeconds: 10,
    restSeconds: 120,
    defaultRestSeconds: 60,
  };

  it('depois do aquecimento usa o intervalo do aquecimento', () => {
    expect(resolveRestSeconds({ ...base, completedStageKind: 'warmup', nextStageKind: 'feeler' })).toBe(30);
  });

  it('depois do ajuste usa o intervalo do ajuste', () => {
    expect(resolveRestSeconds({ ...base, completedStageKind: 'feeler' })).toBe(90);
  });

  it('sem intervalo próprio cai no descanso da série e depois no padrão', () => {
    expect(resolveRestSeconds({ ...base, completedStageKind: 'warmup', warmupRestSeconds: null })).toBe(120);
    expect(
      resolveRestSeconds({ ...base, completedStageKind: 'feeler', feelerRestSeconds: null, restSeconds: null }),
    ).toBe(60);
  });

  it('depois da série de trabalho mantém o descanso da série', () => {
    expect(resolveRestSeconds({ ...base, completedStageKind: null })).toBe(120);
  });
});

describe('resolveEffectiveExerciseDisplay (descrição do exercício após troca)', () => {
  const planExercise = {
    exercise_name: 'Supino inclinado com halteres',
    mapping_id: 46,
    exercise_description: 'Segure um halter em cada mão...',
  };
  const gymLinks = [
    { id: 939, mapping_id: 2, mapping_exercise_description: 'Empurre as alças para frente e para cima...' },
    { id: 828, mapping_id: 46, mapping_exercise_description: 'Segure um halter em cada mão...' },
  ];

  it('com override para outro exercício, usa nome e descrição do exercício novo', () => {
    const result = resolveEffectiveExerciseDisplay({
      planExercise,
      override: { mapping_id: 2, equipment_link_id: 939, exercise_name: 'Supino inclinado na máquina', equipment_name: 'Hammer' },
      gymLinks,
      equipmentLinkId: 939,
    });
    expect(result).toEqual({
      exerciseName: 'Supino inclinado na máquina',
      description: 'Empurre as alças para frente e para cima...',
    });
  });

  it('retomando sem override, usa o exercício já lançado no backend', () => {
    const result = resolveEffectiveExerciseDisplay({
      planExercise,
      loggedRow: {
        mapping_id: 2,
        original_name: 'Supino inclinado na máquina',
        exercise_name: 'Supino inclinado na máquina',
        exercise_description: 'Empurre as alças para frente e para cima...',
      },
      gymLinks,
      equipmentLinkId: 939,
    });
    expect(result.exerciseName).toBe('Supino inclinado na máquina');
    expect(result.description).toBe('Empurre as alças para frente e para cima...');
  });

  it('nunca mostra a descrição da ficha para outro exercício', () => {
    const result = resolveEffectiveExerciseDisplay({
      planExercise,
      override: { mapping_id: 2, equipment_link_id: 939, exercise_name: 'Supino inclinado na máquina', equipment_name: 'Hammer' },
      gymLinks: [],
      equipmentLinkId: 939,
    });
    expect(result.description).toBeNull();
  });

  it('sem troca, usa a descrição do item da ficha', () => {
    const result = resolveEffectiveExerciseDisplay({ planExercise, gymLinks, equipmentLinkId: 828 });
    expect(result).toEqual({ exerciseName: 'Supino inclinado com halteres', description: 'Segure um halter em cada mão...' });
  });
});

describe('técnica avançada pulada conclui o exercício (treino avançado)', () => {
  // Ficha: 1 aquecimento + 1 ajuste + 3 séries de trabalho + 3 mini-blocos de rest-pause.
  const base: AdvancedExerciseCounters = {
    workingTarget: 3,
    loggedNonPreparatory: 0,
    warmupTarget: 1,
    warmupDone: 1,
    feelerTarget: 1,
    feelerDone: 1,
    techniqueTarget: 3,
    techniqueSkipped: 0,
  };
  const at = (patch: Partial<AdvancedExerciseCounters>): AdvancedExerciseCounters => ({ ...base, ...patch });

  it('fluxo sem pular nada: trabalho -> 3 aplicações -> concluído', () => {
    expect(resolveAdvancedSubStage(at({ loggedNonPreparatory: 2 }))).toBeNull();
    expect(resolveAdvancedSubStage(at({ loggedNonPreparatory: 3 }))).toEqual({ kind: 'technique', index: 1, total: 3 });
    expect(resolveAdvancedSubStage(at({ loggedNonPreparatory: 5 }))).toEqual({ kind: 'technique', index: 3, total: 3 });
    expect(isAdvancedExerciseDone(at({ loggedNonPreparatory: 5 }))).toBe(false);
    expect(isAdvancedExerciseDone(at({ loggedNonPreparatory: 6 }))).toBe(true);
    expect(resolveAdvancedSubStage(at({ loggedNonPreparatory: 6 }))).toBeNull();
  });

  it('BUG: pular a ÚLTIMA aplicação conclui o exercício (antes travava em "Série 6 de 3")', () => {
    const state = at({ loggedNonPreparatory: 5, techniqueSkipped: 1 });
    expect(resolveAdvancedSubStage(state)).toBeNull();
    expect(isAdvancedExerciseDone(state)).toBe(true);
  });

  it('pular TODAS as aplicações conclui o exercício logo após as séries de trabalho', () => {
    expect(resolveAdvancedSubStage(at({ loggedNonPreparatory: 3, techniqueSkipped: 2 }))).toEqual({
      kind: 'technique',
      index: 3,
      total: 3,
    });
    expect(isAdvancedExerciseDone(at({ loggedNonPreparatory: 3, techniqueSkipped: 2 }))).toBe(false);
    expect(isAdvancedExerciseDone(at({ loggedNonPreparatory: 3, techniqueSkipped: 3 }))).toBe(true);
  });

  it('pular uma aplicação do MEIO: as demais seguem numeradas e o exercício conclui ao gravar a última', () => {
    // 1ª gravada, 2ª pulada -> oferece a 3ª.
    const afterSkip = at({ loggedNonPreparatory: 4, techniqueSkipped: 1 });
    expect(resolveAdvancedSubStage(afterSkip)).toEqual({ kind: 'technique', index: 3, total: 3 });
    expect(isAdvancedExerciseDone(afterSkip)).toBe(false);
    // 3ª gravada.
    const afterLast = at({ loggedNonPreparatory: 5, techniqueSkipped: 1 });
    expect(isAdvancedExerciseDone(afterLast)).toBe(true);
  });

  it('contador de puladas nunca conclui o exercício com série de TRABALHO faltando', () => {
    const state = at({ loggedNonPreparatory: 2, techniqueSkipped: 3 });
    expect(isAdvancedExerciseDone(state)).toBe(false);
    expect(resolveAdvancedSubStage(state)).toBeNull(); // ainda "Série 3 de 3"
    // contador local "sujo" (maior que o total) não estoura a conta
    expect(isAdvancedExerciseDone(at({ loggedNonPreparatory: 2, techniqueSkipped: 99 }))).toBe(false);
    expect(isAdvancedExerciseDone(at({ loggedNonPreparatory: 3, techniqueSkipped: 99 }))).toBe(true);
  });

  it('aquecimento/ajuste pendentes continuam vindo primeiro e impedem a conclusão', () => {
    const state = at({ warmupDone: 0, feelerDone: 0, loggedNonPreparatory: 3, techniqueSkipped: 3 });
    expect(resolveAdvancedSubStage(state)).toEqual({ kind: 'warmup', index: 1, total: 1 });
    expect(isAdvancedExerciseDone(state)).toBe(false);
  });

  it('exercício sem técnica / sem sub-etapas: só as séries de trabalho contam', () => {
    const plain = at({ warmupTarget: 0, warmupDone: 0, feelerTarget: 0, feelerDone: 0, techniqueTarget: 0 });
    expect(isAdvancedExerciseDone({ ...plain, loggedNonPreparatory: 2 })).toBe(false);
    expect(isAdvancedExerciseDone({ ...plain, loggedNonPreparatory: 3 })).toBe(true);
    expect(isAdvancedExerciseDone({ ...plain, loggedNonPreparatory: 2, techniqueSkipped: 1 })).toBe(false);
  });

  it('retomada COM o estado estacionado: a aplicação pulada continua valendo', () => {
    const server: StoredWorkoutModeSession = { session_id: 7, source: 'plan', workout_plan_id: 3, gym_id: 1 };
    const parked: StoredWorkoutModeSession = {
      ...server,
      advanced_mode: true,
      advanced_stage_progress: { 42: { warmupDone: 1, feelerDone: 1, techniqueSkipped: 1 } },
    };
    const resumed = mergeResumedSession(server, parked);
    const local = resumed.advanced_stage_progress?.[42];
    const state = at({ loggedNonPreparatory: 5, techniqueSkipped: local?.techniqueSkipped ?? 0 });
    expect(isAdvancedExerciseDone(state)).toBe(true);
    expect(resolveAdvancedSubStage(state)).toBeNull();
  });

  it('retomada SEM o estado local (outro aparelho): reoferece a aplicação, nunca "Série N+1"', () => {
    const state = at({ loggedNonPreparatory: 5, techniqueSkipped: 0 });
    expect(isAdvancedExerciseDone(state)).toBe(false);
    expect(resolveAdvancedSubStage(state)).toEqual({ kind: 'technique', index: 3, total: 3 });
  });

  it('troca de equipamento com continuação: progresso zerado no item novo e pular as aplicações conclui a continuação', () => {
    expect(progressAfterEquipmentSwap({ warmupDone: 1 }).techniqueSkipped).toBe(0);
    // Continuação sintética: 2 séries restantes + as 3 aplicações herdadas, ajuste reoferecido.
    const continuation = at({ workingTarget: 2, feelerDone: 0 });
    expect(resolveAdvancedSubStage(continuation)).toEqual({ kind: 'feeler', index: 1, total: 1 });
    const skippedAll = { ...continuation, feelerDone: 1, loggedNonPreparatory: 2, techniqueSkipped: 3 };
    expect(isAdvancedExerciseDone(skippedAll)).toBe(true);
    expect(resolveAdvancedSubStage(skippedAll)).toBeNull();
  });

  it('invariante: exercício pendente SEMPRE tem etapa a exibir dentro do alvo (nunca "Série N+1")', () => {
    for (let logged = 0; logged <= 7; logged += 1) {
      for (let skipped = 0; skipped <= 4; skipped += 1) {
        for (const warmupDone of [0, 1]) {
          for (const feelerDone of [0, 1]) {
            const state = at({ loggedNonPreparatory: logged, techniqueSkipped: skipped, warmupDone, feelerDone });
            const stage = resolveAdvancedSubStage(state);
            const done = isAdvancedExerciseDone(state);
            const workingPending = stage == null && logged < state.workingTarget;
            expect({ state, ok: done ? stage == null : stage != null || workingPending }).toEqual({ state, ok: true });
          }
        }
      }
    }
  });
});

// Cenário sintético baseado em caso real (atividade 50 / ficha 10, 2026-09-25): o usuário trocou o
// exercício do 2º item executado para "Desenvolvimento de ombros na máquina",
// que a ficha já prescrevia no item 101 (o primeiro). O 101 ficou pendente,
// voltou ao fim da fila e o mesmo exercício foi registrado duas vezes.
describe('findPlanItemsCoveredByAnotherSlot (exercício trocado voltava à fila e era registrado 2x)', () => {
  const planItem = (id: number, sequence: number, mappingId: number) =>
    ({
      workout_plan_exercise_id: id,
      sequence,
      mapping_id: mappingId,
      exercise_name: `ex${id}`,
    }) as WorkoutPlanExercise;
  const logged = (slotId: number | null, mappingId: number | null, plannedMappingId: number | null) =>
    ({
      id: 500 + (slotId ?? 0),
      workout_plan_exercise_id: slotId,
      mapping_id: mappingId,
      planned_mapping_id: plannedMappingId,
      sets: [],
    }) as unknown as ExerciseLogRow;

  // 101 = Desenvolvimento de ombros na máquina (mapping 610), item 1 da ficha.
  // 103 = outro exercício (mapping 700), executado em 2º com o exercício do 101.
  const planExercises = [planItem(101, 1, 610), planItem(102, 2, 800), planItem(103, 3, 700)];

  it('marca como coberto o item cujo exercício foi executado no slot de OUTRO item', () => {
    const covered = findPlanItemsCoveredByAnotherSlot({
      planExercises,
      loggedExercises: [logged(102, 800, 800), logged(103, 610, 700)],
    });
    expect([...covered]).toEqual([101]);
  });

  it('não cobre nada quando cada lançamento está no seu próprio slot', () => {
    const covered = findPlanItemsCoveredByAnotherSlot({
      planExercises,
      loggedExercises: [logged(102, 800, 800), logged(103, 700, 700)],
    });
    expect(covered.size).toBe(0);
  });

  it('não cobre um item que já tem lançamento próprio', () => {
    const covered = findPlanItemsCoveredByAnotherSlot({
      planExercises,
      loggedExercises: [logged(101, 610, 610), logged(103, 610, 700)],
    });
    expect(covered.size).toBe(0);
  });

  it('exercício avulso (sem item de ficha) nunca resolve um item da ficha', () => {
    const covered = findPlanItemsCoveredByAnotherSlot({
      planExercises,
      loggedExercises: [logged(null, 610, null)],
    });
    expect(covered.size).toBe(0);
  });

  it('item da ficha sem mapping no vínculo (planned_mapping_id nulo) não cobre nada', () => {
    const covered = findPlanItemsCoveredByAnotherSlot({
      planExercises,
      loggedExercises: [logged(103, 610, null)],
    });
    expect(covered.size).toBe(0);
  });
});

// Replay da atividade 50 (ficha 10, 7 itens) no ponto em que o bug
// apareceu: 6 exercícios executados, e o item 101 — cujo exercício JÁ havia
// sido feito às 09:25 no slot do item 103 — era o único "pendente". Era ele
// que a tela oferecia como "Exercício 1 de 7" às 10:02 e que o usuário
// executou de novo às 10:07 (2 séries a mais do mesmo exercício no
// mesmo aparelho).
describe('atividade 50: o exercício trocado não pode reaparecer no fim da ficha', () => {
  const MAPPINGS: Record<number, number> = { 101: 610, 102: 520, 103: 700, 104: 974, 105: 770, 106: 509, 107: 640 };
  const planExercises = [101, 102, 103, 104, 105, 106, 107].map(
    (id, index) =>
      ({
        workout_plan_exercise_id: id,
        sequence: index + 1,
        mapping_id: MAPPINGS[id],
        exercise_name: `ex${id}`,
      }) as WorkoutPlanExercise,
  );
  const row = (id: number, slotId: number, mappingId: number) =>
    ({
      id,
      workout_plan_exercise_id: slotId,
      mapping_id: mappingId,
      planned_mapping_id: MAPPINGS[slotId],
      sets: [],
    }) as unknown as ExerciseLogRow;

  // Estado real às 10:05, ANTES da execução duplicada das 10:07.
  const loggedExercises = [
    row(592, 102, MAPPINGS[102]),
    row(593, 103, MAPPINGS[101]), // troca: item 103 executado com o exercício do 101
    row(594, 104, MAPPINGS[104]),
    row(595, 105, MAPPINGS[105]),
    row(596, 106, MAPPINGS[106]),
    row(597, 107, MAPPINGS[107]), // encerrado no meio (1 série)
    row(598, 107, 420), // continuação sintética em outro aparelho (2 séries)
  ];

  it('nenhum item da ficha continua pendente — a ficha está concluída', () => {
    const covered = findPlanItemsCoveredByAnotherSlot({ planExercises, loggedExercises });
    const loggedSlots = new Set(loggedExercises.map((logged) => logged.workout_plan_exercise_id));
    const pending = planExercises
      .map((planExercise) => planExercise.workout_plan_exercise_id as number)
      .filter((id) => !loggedSlots.has(id) && !covered.has(id));
    expect(pending).toEqual([]);
    expect(covered.has(101)).toBe(true);
  });

  it('a continuação por troca de equipamento no meio do exercício continua válida (não some item nem cobre outro)', () => {
    const covered = findPlanItemsCoveredByAnotherSlot({ planExercises, loggedExercises });
    // As duas linhas do item 107 (aparelhos diferentes) são trabalho REAL
    // feito em sequência, não duplicação: nada a resolver por cobertura.
    expect([...covered]).toEqual([101]);
  });
});

describe('executionPositionOrdinal ("Exercício 1 de 7" ao lado de "6 concluído(s)")', () => {
  it('conta os concluídos, não a posição do item na ficha', () => {
    // Fila real do print: o item pulado no começo da ficha é o único
    // pendente e continua na posição 1 — `currentIndex + 1` dizia
    // "Exercício 1 de 7" com 6 exercícios já concluídos.
    const items = [{ done: false }, ...Array.from({ length: 6 }, () => ({ done: true }))];
    expect(executionPositionOrdinal(items)).toBe(7);
  });

  it('primeiro exercício da ficha é o 1', () => {
    expect(executionPositionOrdinal([{ done: false }, { done: false }])).toBe(1);
  });
});

// ---------------------------------------------------------------------------
// Fila da execução (cenário sintético baseado em caso real: atividade 50, ficha 10)
// ---------------------------------------------------------------------------

// Simulador da execução de uma ficha no Modo Treino usando as funções REAIS da
// fila (`buildExecutionQueue` + `reorderPendingBySkip` +
// `findPlanItemsCoveredByAnotherSlot` + `findPlanItemForSubstitutedExercise`),
// na mesma ordem em que `PlanExecutionScreen` as usa: a cada passo deriva o
// `progress` do que está gravado no servidor, escolhe o primeiro pendente como
// exercício EXIBIDO e grava a linha de `exercises` que aquele passo produziria.
//
// Serve para responder, executando, as duas perguntas do bug: QUAL item é
// exibido e QUAL `workout_plan_exercise_id` é gravado em cada passo.
type SimOptions = {
  /** `findPlanItemForSubstitutedExercise` (a correção deste PR). */
  jumpEnabled: boolean;
  /** `findPlanItemsCoveredByAnotherSlot` (PR #209). */
  coverEnabled: boolean;
};
type SimOverride = { mapping_id: number; equipment_link_id: number; exercise_name: string };

const ACTIVITY_50_PLAN: WorkoutPlanExercise[] = [
  { workout_plan_exercise_id: 101, sequence: 1, exercise_name: 'Desenvolvimento de ombros na maquina', mapping_id: 12, preferred_equipment_id: 610, sets_count: 4 },
  { workout_plan_exercise_id: 102, sequence: 2, exercise_name: 'Elevacao lateral na maquina', mapping_id: 14, preferred_equipment_id: 52, sets_count: 4 },
  { workout_plan_exercise_id: 103, sequence: 3, exercise_name: 'Elevacao frontal com halteres', mapping_id: 47, preferred_equipment_id: 81, sets_count: 3 },
  { workout_plan_exercise_id: 104, sequence: 4, exercise_name: 'Supino inclinado com barra', mapping_id: 123, preferred_equipment_id: 974, sets_count: 3 },
  { workout_plan_exercise_id: 105, sequence: 5, exercise_name: 'Crucifixo na maquina', mapping_id: 4, preferred_equipment_id: 77, sets_count: 3 },
  { workout_plan_exercise_id: 106, sequence: 6, exercise_name: 'Elevacao lateral com halteres', mapping_id: 93, preferred_equipment_id: 509, sets_count: 3 },
  { workout_plan_exercise_id: 107, sequence: 7, exercise_name: 'Elev. panturrilhas sentada na maquina', mapping_id: 25, preferred_equipment_id: 420, sets_count: 3 },
] as unknown as WorkoutPlanExercise[];

function makePlanExecutionSim(options: SimOptions) {
  const skippedIds = new Set<number>();
  const cancelledIds = new Set<number>();
  const overrides: Record<number, SimOverride> = {};
  const logged: ExerciseLogRow[] = [];
  /** Telas de exercício efetivamente EXIBIDAS, em ordem (sem repetir a mesma). */
  const displayed: string[] = [];
  let priorityId: number | null = null;

  const ordered = () =>
    buildExecutionQueue<WorkoutPlanExercise>({
      planExercises: ACTIVITY_50_PLAN,
      syntheticExercises: [],
      cancelledIds,
    });

  function progress() {
    const covered = options.coverEnabled
      ? findPlanItemsCoveredByAnotherSlot({ planExercises: ordered(), loggedExercises: logged })
      : new Set<number>();
    const base = ordered().map((planExercise) => {
      const id = planExercise.workout_plan_exercise_id as number;
      const loggedRow = logged.find((row) => row.workout_plan_exercise_id === id);
      const loggedCount = loggedRow?.sets?.length ?? 0;
      const targetCount = planExercise.sets_count ?? 1;
      return { planExercise, loggedRow, loggedCount, targetCount, done: covered.has(id) || loggedCount >= targetCount };
    });
    return reorderPendingBySkip(base, (item) => item.planExercise.workout_plan_exercise_id ?? -1, skippedIds, priorityId);
  }

  function current() {
    const cur = progress().find((item) => !item.done) ?? null;
    if (cur) {
      const id = cur.planExercise.workout_plan_exercise_id as number;
      const label = `${id}:${overrides[id]?.exercise_name ?? cur.planExercise.exercise_name}`;
      if (displayed[displayed.length - 1] !== label) displayed.push(label);
    }
    return cur;
  }

  const sim = {
    displayed,
    logged,
    progress,
    current,
    pendingIds: () =>
      progress()
        .filter((item) => !item.done)
        .map((item) => item.planExercise.workout_plan_exercise_id as number),
    /** "Pular este exercício" escolhendo qual fazer agora (`SkipExerciseModal`). */
    skip(chosenId: number) {
      const cur = current();
      skippedIds.add(cur?.planExercise.workout_plan_exercise_id as number);
      priorityId = chosenId;
    },
    /** Escolha no `EquipmentSubstituteModal` (`handleSubstituteSelect`). */
    substitute(choice: SimOverride) {
      const id = current()?.planExercise.workout_plan_exercise_id as number;
      const jumpTargetId = options.jumpEnabled
        ? findPlanItemForSubstitutedExercise({
            planExercises: ordered(),
            chosenMappingId: choice.mapping_id,
            currentWorkoutPlanExerciseId: id,
            isPending: (candidate) =>
              progress().some((item) => !item.done && item.planExercise.workout_plan_exercise_id === candidate),
          })
        : null;
      if (jumpTargetId != null) {
        // Como em `handleSubstituteSelect`: pula para o item prescrito e manda
        // o item atual para o FIM da fila (continua pendente e será exibido).
        skippedIds.add(id);
        priorityId = jumpTargetId;
        return;
      }
      overrides[id] = choice;
    },
    /** Executa o exercício exibido até o fim (grava `exercises` + as séries). */
    execute() {
      const cur = current();
      if (!cur) throw new Error('nada pendente para executar');
      const id = cur.planExercise.workout_plan_exercise_id as number;
      const override = overrides[id];
      logged.push({
        id: 592 + logged.length,
        workout_plan_exercise_id: id,
        mapping_id: override?.mapping_id ?? cur.planExercise.mapping_id ?? null,
        // O backend deriva do vínculo (`wpe.mapping_id`), não do payload.
        planned_mapping_id: ACTIVITY_50_PLAN.find((item) => item.workout_plan_exercise_id === id)?.mapping_id ?? null,
        equipment_id: override?.equipment_link_id ?? cur.planExercise.preferred_equipment_id ?? null,
        original_name: override?.exercise_name ?? cur.planExercise.exercise_name,
        sets: Array.from({ length: cur.targetCount }, (_, index) => ({ set_number: index + 1, set_type: 'work' })),
      } as unknown as ExerciseLogRow);
      if (priorityId === id) priorityId = null;
    },
    runToEnd() {
      for (let guard = 0; guard < 20 && sim.current(); guard += 1) sim.execute();
    },
    rows: () => logged.map((row) => `${row.workout_plan_exercise_id}<-${row.original_name}`),
  };
  return sim;
}

const DESENVOLVIMENTO: SimOverride = {
  mapping_id: 12,
  equipment_link_id: 610,
  exercise_name: 'Desenvolvimento de ombros na maquina',
};

describe('atividade 50: "Pular" NAO desalinha a fila', () => {
  // Hipótese testada e REFUTADA por execução: "Pular" manda o item para o fim
  // da fila de pendentes sem mudar sua posição, mas `reorderPendingBySkip`
  // reordena os OBJETOS dos slots pendentes — o item exibido e o item gravado
  // são sempre o mesmo. Pular o 1º de 7 e seguir a ficha inteira grava cada
  // exercício no seu próprio slot, sem nenhum item consumido sem aparecer.
  it('pular o 1o item e seguir a ficha grava cada exercicio no seu proprio slot', () => {
    const sim = makePlanExecutionSim({ jumpEnabled: true, coverEnabled: true });
    sim.skip(102);
    sim.runToEnd();

    expect(sim.displayed).toEqual([
      '101:Desenvolvimento de ombros na maquina',
      '102:Elevacao lateral na maquina',
      '103:Elevacao frontal com halteres',
      '104:Supino inclinado com barra',
      '105:Crucifixo na maquina',
      '106:Elevacao lateral com halteres',
      '107:Elev. panturrilhas sentada na maquina',
      '101:Desenvolvimento de ombros na maquina',
    ]);
    expect(sim.logged.map((row) => row.workout_plan_exercise_id)).toEqual([102, 103, 104, 105, 106, 107, 101]);
    expect(sim.logged.every((row) => row.mapping_id === row.planned_mapping_id)).toBe(true);
  });
});

describe('atividade 50: substituir pelo exercicio de outro item da ficha e PULAR para ele', () => {
  // Assinatura do caso real (cenário sintético): o usuário pulou o 101 (Desenvolvimento), executou
  // o 102 e, quando o 103 (Elevação frontal) apareceu, usou a substituição de
  // equipamento para voltar ao Desenvolvimento. A substituição virava override
  // no item 103 -> `exercises` com `mapping_id=12` e
  // `workout_plan_exercise_id=103`, a Elevação frontal nunca executada e o 101
  // ainda pendente (voltando ao fim da fila).
  it('a execucao vai para o slot do item prescrito (101), nao para o slot atual (103)', () => {
    const sim = makePlanExecutionSim({ jumpEnabled: true, coverEnabled: true });
    sim.skip(102);
    sim.execute();
    sim.substitute(DESENVOLVIMENTO);
    sim.runToEnd();

    expect(sim.rows()).toEqual([
      '102<-Elevacao lateral na maquina',
      '101<-Desenvolvimento de ombros na maquina',
      '104<-Supino inclinado com barra',
      '105<-Crucifixo na maquina',
      '106<-Elevacao lateral com halteres',
      '107<-Elev. panturrilhas sentada na maquina',
      // Pendente até o fim da ficha, mas EXECUTADO no seu próprio slot.
      '103<-Elevacao frontal com halteres',
    ]);
    // Nenhum vínculo cruzado: nada parece "substituição" para progressão,
    // dashboard e recordes.
    expect(sim.logged.filter((row) => row.mapping_id !== row.planned_mapping_id)).toEqual([]);
    // Nenhum exercício executado duas vezes.
    expect(new Set(sim.logged.map((row) => row.mapping_id)).size).toBe(7);
  });

  it('o item atual (103) continua pendente e volta a ser EXIBIDO - nada e consumido sem aparecer', () => {
    const sim = makePlanExecutionSim({ jumpEnabled: true, coverEnabled: true });
    sim.skip(102);
    sim.execute();
    sim.substitute(DESENVOLVIMENTO);
    // Logo depois da escolha, a tela é o item prescrito (101) — e não um
    // override em cima do 103.
    expect(sim.current()?.planExercise.workout_plan_exercise_id).toBe(101);
    expect(sim.pendingIds()).toContain(103);
    sim.runToEnd();

    // A Elevação frontal apareceu (antes e depois do desvio) e foi executada
    // com o exercício da ficha.
    expect(sim.displayed.filter((label) => label.startsWith('103:'))).toEqual([
      '103:Elevacao frontal com halteres',
      '103:Elevacao frontal com halteres',
    ]);
    // INVARIANTE: todo item da ficha que terminou concluído foi exibido.
    const displayedIds = new Set(sim.displayed.map((label) => Number(label.split(':')[0])));
    const doneIds = sim
      .progress()
      .filter((item) => item.done)
      .map((item) => item.planExercise.workout_plan_exercise_id as number);
    expect(doneIds.filter((id) => !displayedIds.has(id))).toEqual([]);
    expect(doneIds).toHaveLength(7);
  });

  it('sem a correcao: linha no slot errado e item da ficha consumido sem nunca aparecer', () => {
    // Mesmos passos com a decisão de "pular para o item prescrito"
    // neutralizada (o código anterior a este PR, já com o #209): a assinatura
    // de produção reaparece inteira.
    const sim = makePlanExecutionSim({ jumpEnabled: false, coverEnabled: true });
    sim.skip(102);
    sim.execute();
    sim.substitute(DESENVOLVIMENTO);
    sim.runToEnd();

    // A linha do Desenvolvimento nasce no slot do 103 (executado 12 x prescrito 47).
    const crossSlot = sim.logged.filter((row) => row.mapping_id !== row.planned_mapping_id);
    expect(crossSlot.map((row) => [row.workout_plan_exercise_id, row.mapping_id, row.planned_mapping_id])).toEqual([
      [103, 12, 47],
    ]);
    // A Elevação frontal nunca é executada: 6 linhas numa ficha de 7 itens.
    expect(sim.logged).toHaveLength(6);
    // E o 101 é dado como concluído pelo #209 sem nunca ter sido exibido de
    // novo nem executado: a duplicata some, o defeito continua.
    expect(sim.displayed.filter((label) => label.startsWith('101:'))).toHaveLength(1);
    expect(sim.progress().every((item) => item.done)).toBe(true);
  });

  it('sem a correcao e sem o #209: o exercicio e executado duas vezes (dado real de 25/09)', () => {
    const sim = makePlanExecutionSim({ jumpEnabled: false, coverEnabled: false });
    sim.skip(102);
    sim.execute();
    sim.substitute(DESENVOLVIMENTO);
    sim.runToEnd();

    expect(sim.rows()).toEqual([
      '102<-Elevacao lateral na maquina',
      '103<-Desenvolvimento de ombros na maquina',
      '104<-Supino inclinado com barra',
      '105<-Crucifixo na maquina',
      '106<-Elevacao lateral com halteres',
      '107<-Elev. panturrilhas sentada na maquina',
      '101<-Desenvolvimento de ombros na maquina',
    ]);
  });
});

describe('findPlanItemForSubstitutedExercise', () => {
  const plan = ACTIVITY_50_PLAN;
  const allPending = () => true;

  it('devolve o item pendente cujo exercicio foi escolhido', () => {
    expect(
      findPlanItemForSubstitutedExercise({
        planExercises: plan,
        chosenMappingId: 12,
        currentWorkoutPlanExerciseId: 103,
        isPending: allPending,
      }),
    ).toBe(101);
  });

  it('exercicio que nao esta na ficha continua sendo substituicao normal', () => {
    expect(
      findPlanItemForSubstitutedExercise({
        planExercises: plan,
        chosenMappingId: 999,
        currentWorkoutPlanExerciseId: 103,
        isPending: allPending,
      }),
    ).toBeNull();
  });

  it('item ja concluido nao e alvo de pulo (repetir um exercicio ja feito e escolha do usuario)', () => {
    expect(
      findPlanItemForSubstitutedExercise({
        planExercises: plan,
        chosenMappingId: 12,
        currentWorkoutPlanExerciseId: 103,
        isPending: (id) => id !== 101,
      }),
    ).toBeNull();
  });

  it('escolher o exercicio do proprio item (trocar so o aparelho) nao e pulo', () => {
    expect(
      findPlanItemForSubstitutedExercise({
        planExercises: plan,
        chosenMappingId: 47,
        currentWorkoutPlanExerciseId: 103,
        isPending: allPending,
      }),
    ).toBeNull();
  });
});

describe('reorderPendingBySkip (fila real do modo treino)', () => {
  const slot = (id: number, done: boolean) => ({ id, done });
  const keyOf = (value: { id: number }) => value.id;

  it('o item pulado vai para o fim da fila e o escolhido assume a vez', () => {
    const items = [slot(1, false), slot(2, false), slot(3, false)];
    expect(reorderPendingBySkip(items, keyOf, new Set([1]), 2).map((value) => value.id)).toEqual([2, 3, 1]);
  });

  it('itens concluidos nao mudam de posicao', () => {
    const items = [slot(1, false), slot(2, true), slot(3, false)];
    expect(reorderPendingBySkip(items, keyOf, new Set([1]), null).map((value) => value.id)).toEqual([3, 2, 1]);
  });
});

// M76 (Lote 1b, C5/C7): o teto e o passo do par em execução vêm prontos do
// servidor; a tela só escolhe a fonte certa.
describe('resolveSessionPairLimits', () => {
  const planItem = {
    workout_plan_exercise_id: 7,
    sequence: 1,
    exercise_name: 'Supino',
    load_increment_kg: 1,
    prescription: { effective_rpe_ceiling: 9, load_anchor_kg: 60, warmup_loads_kg: [12, 24] },
    safety_overlay: { effective_rpe_ceiling: 9, failure_allowed: false, max_safe_rpe: 10, safety_source: null, clamps: [] },
  } as unknown as WorkoutPlanExercise;

  it('item da ficha: teto da prescrição exibida (já composta pelo servidor), passo e cargas da API', () => {
    const limits = resolveSessionPairLimits({ planExercise: planItem, hasOverride: false, pairSuggestion: null, syntheticExtra: null });
    expect(limits.rpeCeiling).toBe(9);
    expect(limits.loadIncrementKg).toBe(1);
    expect(limits.loadPlan?.warmup_loads_kg).toEqual([12, 24]);
  });

  it('ficha sem snapshot (LEGACY): teto do overlay do par', () => {
    const legacy = { ...planItem, prescription: null } as WorkoutPlanExercise;
    const limits = resolveSessionPairLimits({ planExercise: legacy, hasOverride: false, pairSuggestion: null, syntheticExtra: null });
    expect(limits.rpeCeiling).toBe(9);
    expect(limits.loadPlan).toBeNull();
  });

  it('equipamento trocado na sessão: teto e passo do suggested-progression do par novo, sem as cargas da ficha', () => {
    const pairSuggestion = {
      reps_target: '10-12',
      load_target: '40',
      rpe_target: '8',
      progression_note: null,
      has_history: true,
      effective_rpe_ceiling: 8,
      load_increment_kg: 5,
    };
    const limits = resolveSessionPairLimits({ planExercise: planItem, hasOverride: true, pairSuggestion, syntheticExtra: null });
    expect(limits).toEqual({ rpeCeiling: 8, loadIncrementKg: 5, loadPlan: null });
    // Enquanto a API não respondeu, não herda o teto do par antigo.
    expect(
      resolveSessionPairLimits({ planExercise: planItem, hasOverride: true, pairSuggestion: undefined, syntheticExtra: null }),
    ).toEqual({ rpeCeiling: null, loadIncrementKg: null, loadPlan: null });
  });

  it('continuação sintética: o que a API devolveu na hora da troca', () => {
    const extra = buildSyntheticContinuation({
      localId: -1000,
      current: advancedPlanItem,
      remainingSets: 2,
      choice,
      targets: { ...targets, load_increment_kg: 2.27, effective_rpe_ceiling: 9, failure_allowed: false },
      existingExtras: [],
    });
    expect(syntheticExtraToPlanExercise(extra).load_increment_kg).toBe(2.27);
    const limits = resolveSessionPairLimits({
      planExercise: syntheticExtraToPlanExercise(extra),
      hasOverride: false,
      pairSuggestion: null,
      syntheticExtra: extra,
    });
    expect(limits).toEqual({ rpeCeiling: 9, loadIncrementKg: 2.27, loadPlan: null });
  });
});

describe('originalPlanItemId (M97-D2: item trocado na sugestão do par)', () => {
  const extras = [{ localId: -1, originalWorkoutPlanExerciseId: 42 }];
  it('linha da ficha: o próprio id', () => {
    expect(originalPlanItemId(7, extras)).toBe(7);
  });
  it('continuação sintética: o item original que ela substitui', () => {
    expect(originalPlanItemId(-1, extras)).toBe(42);
    expect(originalPlanItemId(-2, extras)).toBeNull();
  });
  it('sem item: null', () => {
    expect(originalPlanItemId(null, extras)).toBeNull();
    expect(originalPlanItemId(undefined, extras)).toBeNull();
  });
});
