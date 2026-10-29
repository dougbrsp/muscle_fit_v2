// Núcleo compartilhado do Modo Treino (PR B da Evolução ZEPP): o MESMO bundle
// que vai para o relógio (`npm run build:workout-core`) é compilado aqui, em
// memória, e testado por fora — texto e comportamento.
//
// GOLDEN (`workout-mode-core.golden.json`, commitado): o mesmo arquivo roda
// no vitest (abaixo), no relógio e no `node --test` do repositório Zepp.
// Formato (contrato com o repositório Zepp, PR Z0): um ARRAY JSON
//
//   [ { "name": "fila: ...", "fn": "reorderPendingBySkipByPlanItem", "input": [arg1, arg2], "expected": <JSON> }, ... ]
//
// - `fn`: nome de uma função EXPORTADA pelo bundle.
// - `input`: lista de argumentos POSICIONAIS, só JSON (um valor que não seja
//   array vira argumento único; aqui todos são listas). Funções que pedem
//   `Set`/callback entram pelos adaptadores JSON do núcleo
//   (`workout-mode-core/json-api.ts`).
// - `expected`: o retorno passado por `JSON.parse(JSON.stringify(r))`; a
//   comparação é `deepStrictEqual`. Nenhum caso devolve `undefined`, função
//   ou Date (o teste abaixo garante).
//
// Runner mínimo em qualquer cliente:
//
//   const args = Array.isArray(c.input) ? c.input : [c.input];
//   deepStrictEqual(JSON.parse(JSON.stringify(core[c.fn](...args))), c.expected);
//
// Os casos são gerados pelas definições abaixo (os números das entradas vêm
// das constantes exportadas pelo núcleo, nunca escritos à mão quando são
// regra). Mudou o comportamento DE PROPÓSITO? Regenere e revise o diff:
//
//   UPDATE_GOLDEN=1 npx vitest run src/lib/workout-mode-core.test.ts
//
// Sem a variável (CI), o teste FALHA se o golden commitado divergir.

import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';

import { buildWorkoutCore, goldenModuleSource } from '../../scripts/build-workout-core.mjs';
import motorConstants from './motor-constants.generated.json';
import parity from './session-loads.parity.json';
import * as source from './workout-mode-core/index';

type CoreModule = typeof source;
type GoldenCase = { name: string; fn: string; input: unknown[]; expected: unknown };

const GOLDEN_PATH = path.join(path.dirname(fileURLToPath(import.meta.url)), 'workout-mode-core.golden.json');
const FORBIDDEN_GLOBALS = ['window', 'document', 'localStorage', 'fetch', 'Intl', 'process'];

let bundleCode = '';
let bundleBytes = 0;
let bundle: CoreModule;
let tempDir = '';

beforeAll(async () => {
  const { code, meta } = await buildWorkoutCore();
  bundleCode = code;
  bundleBytes = meta.bytes;
  tempDir = mkdtempSync(path.join(tmpdir(), 'workout-mode-core-'));
  const file = path.join(tempDir, 'workout-mode-core.mjs');
  writeFileSync(file, code);
  bundle = (await import(/* @vite-ignore */ pathToFileURL(file).href)) as CoreModule;
}, 60_000);

afterAll(() => {
  if (tempDir) rmSync(tempDir, { recursive: true, force: true });
});

function call(core: CoreModule, testCase: Pick<GoldenCase, 'fn' | 'input'>): unknown {
  const fn = (core as unknown as Record<string, unknown>)[testCase.fn];
  if (typeof fn !== 'function') throw new Error(`função ausente no núcleo: ${testCase.fn}`);
  const args = Array.isArray(testCase.input) ? testCase.input : [testCase.input];
  const out = (fn as (...args: unknown[]) => unknown)(...args);
  if (out === undefined) throw new Error(`caso devolve undefined (fora do contrato do golden): ${testCase.fn}`);
  return JSON.parse(JSON.stringify(out));
}

// ---------------------------------------------------------------------------
// Fixtures (ficha de exemplo) e definições dos casos do golden
// ---------------------------------------------------------------------------

const PEITO = {
  workout_plan_exercise_id: 11,
  sequence: 1,
  exercise_name: 'Supino reto',
  mapping_id: 101,
  muscle_primary: 'Peitoral',
  muscle_secondary: 'Tríceps',
  equipment_name: 'Banco reto',
  sets_count: 3,
  reps_target: '8-12',
  rest_seconds: 90,
};
const TRICEPS = {
  workout_plan_exercise_id: 12,
  sequence: 2,
  exercise_name: 'Tríceps corda',
  mapping_id: 102,
  muscle_primary: 'Tríceps',
  sets_count: 3,
  rest_seconds: 60,
};
const OMBRO_DROP = {
  workout_plan_exercise_id: 13,
  sequence: 3,
  exercise_name: 'Elevação lateral',
  mapping_id: 103,
  muscle_primary: ' Ombros ',
  sets_count: 3,
  rest_seconds: 60,
  advanced_technique: 'drop_set',
  advanced_technique_applications: 2,
};
const COSTAS_AVANCADO = {
  workout_plan_exercise_id: 14,
  sequence: 4,
  exercise_name: 'Remada baixa',
  mapping_id: 104,
  muscle_primary: 'Costas',
  muscle_secondary: 'Bíceps',
  equipment_name: 'Polia baixa',
  sets_count: 3,
  rest_seconds: 120,
  warmup_sets_count: 2,
  warmup_reps: 12,
  warmup_rest_seconds: 45,
  feeler_set_sets_count: 2,
  feeler_set_reps: 6,
  feeler_set_rest_seconds: 75,
  advanced_technique: 'rest_pause',
  advanced_technique_applications: 3,
  advanced_technique_duration_seconds: 15,
};
const PLAN = [PEITO, TRICEPS, OMBRO_DROP, COSTAS_AVANCADO];
// PR B4: um item por configuração avançada, e o resíduo que a segurança deixa
// ao remover a técnica na geração (código NULL, aplicações/duração preenchidas).
const SO_AQUECIMENTO = { ...TRICEPS, warmup_sets_count: 2, warmup_reps: 12, warmup_rest_seconds: 45 };
const SO_AJUSTE = { ...TRICEPS, feeler_set_sets_count: 1, feeler_set_reps: 6, feeler_set_rest_seconds: 75 };
const TECNICA_REMOVIDA = {
  ...OMBRO_DROP,
  advanced_technique: null,
  advanced_technique_applications: 2,
  advanced_technique_duration_seconds: 15,
};

// Lote T5 (medidas de série): item por tempo (prancha, 3 × 60 s) e de máximo
// de reps com janela. `PRANCHA_RESIDUO` simula um item por tempo com campos
// avançados (o servidor recusa; o núcleo também não o trata como avançado).
const MEASURES = source.MOTOR_CONSTANTS.set_measures.values;
const MEASURE_LIMITS = source.MOTOR_CONSTANTS.set_measures.limits;
const PRANCHA = {
  workout_plan_exercise_id: 15,
  sequence: 5,
  exercise_name: 'Prancha',
  mapping_id: 106,
  muscle_primary: 'Abdômen',
  sets_count: 3,
  reps_target: null,
  rest_seconds: 60,
  measure: MEASURES.duration,
  duration_seconds_target: 60,
  time_window_seconds: null,
};
const MAXIMO_JANELA = {
  ...TRICEPS,
  reps_target: null,
  measure: MEASURES.max_reps,
  duration_seconds_target: null,
  time_window_seconds: 30,
};
const PRANCHA_RESIDUO = { ...PRANCHA, warmup_reps: 12, advanced_technique: 'drop_set' };

const SWAP_CHOICE = { exercise_name: 'Remada máquina', mapping_id: 105, equipment_link_id: 7, equipment_name: 'Máquina remada' };
const SWAP_TARGETS = {
  reps_target: '8-10',
  load_target: '60kg',
  rpe_target: '8',
  load_increment_kg: 5,
  effective_rpe_ceiling: 9,
  failure_allowed: false,
};

// Prescrição congelada e resposta de `GET /exercises/suggested-progression`
// (só os campos que o núcleo lê).
const PRESCRIPTION = { effective_rpe_target: 8, effective_rpe_ceiling: 8, load_anchor_kg: 60 };
const PAIR_SUGGESTION = {
  reps_target: ' 8-10 ',
  load_target: '62.5kg',
  rpe_target: '8',
  progression_note: ' Subir 2,5 kg ',
  has_history: true,
};

function counters(overrides: Partial<source.AdvancedExerciseCounters>): source.AdvancedExerciseCounters {
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

function restParams(overrides: Record<string, unknown>) {
  return {
    nextStageKind: null,
    isExerciseTransition: false,
    completedStageKind: null,
    warmupRestSeconds: COSTAS_AVANCADO.warmup_rest_seconds,
    feelerRestSeconds: COSTAS_AVANCADO.feeler_set_rest_seconds,
    techniqueDurationSeconds: COSTAS_AVANCADO.advanced_technique_duration_seconds,
    restSeconds: COSTAS_AVANCADO.rest_seconds,
    defaultRestSeconds: source.DEFAULT_REST_SECONDS,
    ...overrides,
  };
}

function caseDefinitions(): Array<Omit<GoldenCase, 'expected'>> {
  const continuation = source.buildSyntheticContinuation({
    localId: -1,
    current: COSTAS_AVANCADO,
    remainingSets: 2,
    choice: SWAP_CHOICE,
    targets: SWAP_TARGETS,
    existingExtras: [],
  });
  const syntheticItem = source.syntheticExtraToPlanExercise(continuation);
  const progressWithPeitoDone = [
    { done: true, planExercise: PEITO },
    { done: false, planExercise: TRICEPS },
    { done: false, planExercise: OMBRO_DROP },
    { done: false, planExercise: COSTAS_AVANCADO },
  ];
  const activated = source.collectActivatedMuscleGroups(progressWithPeitoDone);
  const pendingWarmup = { first: 'pending', second: 'pending' };
  const anchor = 80;
  const step = source.MOTOR_CONSTANTS.session_loads.default_load_step_kg;

  return [
    // -- Fila com pulo ------------------------------------------------------
    {
      name: 'fila: ordem por sequence, sem os cancelados, com a continuação sintética no fim',
      fn: 'buildExecutionQueueFromIds',
      input: [{ planExercises: [COSTAS_AVANCADO, PEITO, OMBRO_DROP, TRICEPS], syntheticExercises: [syntheticItem], cancelledIds: [13] }],
    },
    {
      name: 'fila: pulado vai para o fim, concluído não se move',
      fn: 'reorderPendingBySkipByPlanItem',
      input: [progressWithPeitoDone, [12], null],
    },
    {
      name: 'fila: escolhido no modal vem primeiro, pulado continua no fim',
      fn: 'reorderPendingBySkipByPlanItem',
      input: [progressWithPeitoDone, [12], 14],
    },
    { name: 'fila: posição exibida = concluídos + 1', fn: 'executionPositionOrdinal', input: [progressWithPeitoDone] },

    // -- Troca de equipamento antes e depois da 1ª série ---------------------
    {
      name: 'troca antes da 1ª série: override no próprio item',
      fn: 'shouldSwapAsOverride',
      input: [{ hasLoggedRow: false, workoutPlanExerciseId: 14 }],
    },
    {
      name: 'troca antes da 1ª série numa continuação sintética: não é override',
      fn: 'shouldSwapAsOverride',
      input: [{ hasLoggedRow: false, workoutPlanExerciseId: -1 }],
    },
    {
      name: 'troca depois da 1ª série: encerra o item e cria continuação',
      fn: 'shouldSwapAsOverride',
      input: [{ hasLoggedRow: true, workoutPlanExerciseId: 14 }],
    },
    {
      name: 'troca depois da 1ª série: continuação herda a configuração avançada e o vínculo com a ficha',
      fn: 'buildSyntheticContinuation',
      input: [{ localId: -1, current: COSTAS_AVANCADO, remainingSets: 2, choice: SWAP_CHOICE, targets: SWAP_TARGETS, existingExtras: [] }],
    },
    {
      name: 'troca de novo a partir da continuação: mantém o item ORIGINAL da ficha',
      fn: 'buildSyntheticContinuation',
      input: [{ localId: -2, current: syntheticItem, remainingSets: 1, choice: SWAP_CHOICE, targets: SWAP_TARGETS, existingExtras: [continuation] }],
    },
    { name: 'troca: continuação vira item virtual da fila', fn: 'syntheticExtraToPlanExercise', input: [continuation] },
    { name: 'troca: aquecimento continua valendo, ajuste volta', fn: 'progressAfterEquipmentSwap', input: [{ warmupDone: 2 }] },
    {
      name: 'troca por exercício que a ficha já prescreve (pendente): pula para ele',
      fn: 'findPlanItemForSubstitutedExerciseFromIds',
      input: [{ planExercises: PLAN, chosenMappingId: 102, currentWorkoutPlanExerciseId: 11, pendingIds: [12, 13, 14] }],
    },
    {
      name: 'troca por exercício da ficha já concluído: override normal',
      fn: 'findPlanItemForSubstitutedExerciseFromIds',
      input: [{ planExercises: PLAN, chosenMappingId: 102, currentWorkoutPlanExerciseId: 11, pendingIds: [13, 14] }],
    },

    // -- Aquecimento genérico -----------------------------------------------
    { name: 'aquecimento genérico: grupos ativados pelos concluídos', fn: 'collectActivatedMuscleGroups', input: [progressWithPeitoDone] },
    {
      name: 'aquecimento genérico: grupo já ativado como secundário não pede aquecimento',
      fn: 'needsGenericWarmup',
      input: [TRICEPS.muscle_primary, activated],
    },
    {
      name: 'aquecimento genérico: grupo novo (normalizado) pede aquecimento',
      fn: 'needsGenericWarmup',
      input: [OMBRO_DROP.muscle_primary, activated],
    },
    { name: 'aquecimento genérico: sem grupo primário não pede', fn: 'needsGenericWarmup', input: [null, []] },
    {
      name: 'aquecimento genérico: estado reconciliado com 1 aquecimento já gravado',
      fn: 'resolveGenericWarmupState',
      input: [{ workoutPlanExerciseId: 13, local: null, loggedSets: [{ set_type: 'warmup' }] }],
    },
    {
      name: 'aquecimento genérico: pulado localmente + gravado preenche a outra posição',
      fn: 'resolveGenericWarmupState',
      input: [{ workoutPlanExerciseId: 13, local: { first: 'skipped', second: 'pending' }, loggedSets: [{ set_type: 'Warm-up' }] }],
    },
    {
      name: 'aquecimento genérico: sem item da ficha fica resolvido',
      fn: 'resolveGenericWarmupState',
      input: [{ workoutPlanExerciseId: null, local: null, loggedSets: [] }],
    },
    {
      name: 'aquecimento genérico: aparece no modo normal antes da 1ª série',
      fn: 'shouldShowGenericWarmup',
      input: [{ advancedMode: false, hasCurrent: true, loggedCount: 0, workoutPlanExerciseId: 13, needsWarmup: true, warmupState: pendingWarmup }],
    },
    {
      name: 'aquecimento genérico: nunca no modo avançado (vale o configurado)',
      fn: 'shouldShowGenericWarmup',
      input: [{ advancedMode: true, hasCurrent: true, loggedCount: 0, workoutPlanExerciseId: 13, needsWarmup: true, warmupState: pendingWarmup }],
    },
    {
      name: 'aquecimento genérico: some depois da 1ª série',
      fn: 'shouldShowGenericWarmup',
      input: [{ advancedMode: false, hasCurrent: true, loggedCount: 1, workoutPlanExerciseId: 13, needsWarmup: true, warmupState: pendingWarmup }],
    },
    {
      name: 'aquecimento genérico: some com as 2 séries resolvidas',
      fn: 'shouldShowGenericWarmup',
      input: [{ advancedMode: false, hasCurrent: true, loggedCount: 0, workoutPlanExerciseId: 13, needsWarmup: true, warmupState: { first: 'done', second: 'skipped' } }],
    },
    { name: 'aquecimento genérico: cargas pelo espelho (sem plano)', fn: 'genericWarmupLoads', input: [anchor] },
    { name: 'aquecimento genérico: 1ª série a gravar (espelho)', fn: 'genericWarmupSet', input: ['first', anchor, { step }] },
    {
      name: 'aquecimento genérico: 2ª série a gravar lê a carga da API',
      fn: 'genericWarmupSet',
      input: ['second', anchor, { plan: { load_anchor_kg: anchor, warmup_loads_kg: [15, 30] }, step }],
    },

    // -- Aquecimento configurado (modo avançado) -----------------------------
    {
      name: 'aquecimento configurado: 2ª série de aquecimento',
      fn: 'resolveAdvancedSubStage',
      input: [counters({ warmupTarget: 2, warmupDone: 1, feelerTarget: 2, techniqueTarget: 3 })],
    },
    { name: 'aquecimento configurado: a 1ª carga em todas as séries', fn: 'warmupLoad', input: [anchor, { step }] },
    {
      name: 'aquecimento configurado: rótulo da próxima série',
      fn: 'resolveNextSetPreview',
      input: [{ isExerciseTransition: false, stage: { kind: 'warmup', index: 2, total: 2 }, workingSetNumber: 1, workingSetTotal: 3, weightKg: 15 }],
    },

    // -- Série de ajuste (feeler) -------------------------------------------
    {
      name: 'ajuste: 2ª série de ajuste depois do aquecimento',
      fn: 'resolveAdvancedSubStage',
      input: [counters({ warmupTarget: 2, warmupDone: 2, feelerTarget: 2, feelerDone: 1, techniqueTarget: 3 })],
    },
    { name: 'ajuste: 1ª carga pelo espelho', fn: 'feelerSetLoad', input: [anchor, 1, { step }] },
    {
      name: 'ajuste: 2ª carga lida da API',
      fn: 'feelerSetLoad',
      input: [anchor, 2, { plan: { load_anchor_kg: anchor, feeler_loads_kg: [40, 57.5] }, step }],
    },
    { name: 'ajuste: espelho no passo do equipamento', fn: 'mirrorFeelerLoad', input: [100, 3, 5] },
    {
      name: 'ajuste: rótulo da próxima série',
      fn: 'resolveNextSetPreview',
      input: [{ isExerciseTransition: false, stage: { kind: 'feeler', index: 1, total: 2 }, workingSetNumber: 1, workingSetTotal: 3, weightKg: 40 }],
    },

    // -- Rest-pause e drop-set ----------------------------------------------
    { name: 'técnica: rest-pause vira sub-etapa', fn: 'techniqueStageTarget', input: [COSTAS_AVANCADO] },
    { name: 'técnica: drop-set vira sub-etapa', fn: 'techniqueStageTarget', input: [OMBRO_DROP] },
    {
      name: 'técnica: FST-7 é série de trabalho comum',
      fn: 'techniqueStageTarget',
      input: [{ advanced_technique: 'fst7', advanced_technique_applications: 7 }],
    },
    {
      name: 'técnica: família além da falha não vira sub-etapa',
      fn: 'techniqueStageTarget',
      input: [{ advanced_technique: 'forced_reps', advanced_technique_applications: 2 }],
    },
    {
      name: 'rest-pause: 1º mini-bloco depois das séries de trabalho',
      fn: 'resolveAdvancedSubStage',
      input: [counters({ loggedNonPreparatory: 3, techniqueTarget: 3 })],
    },
    {
      name: 'rest-pause: 1 gravado + 1 pulado -> 3º mini-bloco',
      fn: 'resolveAdvancedSubStage',
      input: [counters({ loggedNonPreparatory: 4, techniqueTarget: 3, techniqueSkipped: 1 })],
    },
    {
      name: 'rest-pause: pular a última aplicação conclui o exercício',
      fn: 'isAdvancedExerciseDone',
      input: [counters({ loggedNonPreparatory: 5, techniqueTarget: 3, techniqueSkipped: 1 })],
    },
    {
      name: 'rest-pause: puladas não concluem com trabalho faltando',
      fn: 'isAdvancedExerciseDone',
      input: [counters({ loggedNonPreparatory: 2, techniqueTarget: 3, techniqueSkipped: 3 })],
    },
    { name: 'rest-pause: repete a carga de trabalho sem arredondar', fn: 'techniqueApplicationLoad', input: ['rest_pause', 51.3, 2, { step }] },
    { name: 'drop-set: 1º drop no passo do equipamento', fn: 'techniqueApplicationLoad', input: ['drop_set', 50, 1, { step }] },
    { name: 'drop-set: 2º drop composto', fn: 'techniqueApplicationLoad', input: ['drop_set', 50, 2, { step }] },
    {
      name: 'drop-set: carga lida da API quando a âncora é a prescrita',
      fn: 'techniqueApplicationLoad',
      input: ['drop_set', 50, 1, { plan: { load_anchor_kg: 50, application_loads_kg: [41, 33] }, step }],
    },
    { name: 'rest-pause: substantivo da aplicação', fn: 'getAdvancedTechniqueApplicationNoun', input: ['rest_pause'] },
    { name: 'drop-set: substantivo da aplicação', fn: 'getAdvancedTechniqueApplicationNoun', input: ['drop_set'] },
    {
      name: 'drop-set: rótulo do próximo drop',
      fn: 'resolveNextSetPreview',
      input: [{ isExerciseTransition: false, stage: { kind: 'technique', index: 2, total: 2 }, techniqueApplicationNoun: 'Drop', workingSetNumber: 4, workingSetTotal: 3, weightKg: 32.5 }],
    },
    {
      name: 'próxima série: trabalho "Série N de S"',
      fn: 'resolveNextSetPreview',
      input: [{ isExerciseTransition: false, stage: null, workingSetNumber: 2, workingSetTotal: 3, weightKg: 60 }],
    },
    {
      name: 'próxima série: acima da meta some o "de N"',
      fn: 'resolveNextSetPreview',
      input: [{ isExerciseTransition: false, stage: null, workingSetNumber: 4, workingSetTotal: 3, weightKg: 60 }],
    },
    {
      name: 'próxima série: transição de exercício não mostra número',
      fn: 'resolveNextSetPreview',
      input: [{ isExerciseTransition: true, stage: null, workingSetNumber: 1, workingSetTotal: 3, weightKg: 60 }],
    },

    // -- Descanso por etapa -------------------------------------------------
    { name: 'descanso: depois do aquecimento usa o do aquecimento', fn: 'resolveRestSeconds', input: [restParams({ completedStageKind: 'warmup', nextStageKind: 'warmup' })] },
    { name: 'descanso: depois do ajuste usa o do ajuste', fn: 'resolveRestSeconds', input: [restParams({ completedStageKind: 'feeler' })] },
    { name: 'descanso: antes do mini-bloco usa a pausa da técnica', fn: 'resolveRestSeconds', input: [restParams({ nextStageKind: 'technique' })] },
    {
      name: 'descanso: entre aplicações da técnica também usa a pausa',
      fn: 'resolveRestSeconds',
      input: [restParams({ completedStageKind: 'technique', nextStageKind: 'technique' })],
    },
    {
      name: 'descanso: transição de exercício usa o da série',
      fn: 'resolveRestSeconds',
      input: [restParams({ nextStageKind: 'technique', isExerciseTransition: true })],
    },
    {
      name: 'descanso: aquecimento sem descanso próprio cai no da série',
      fn: 'resolveRestSeconds',
      input: [restParams({ completedStageKind: 'warmup', warmupRestSeconds: null })],
    },
    {
      name: 'descanso: sem descanso na ficha cai no default do backend',
      fn: 'resolveRestSeconds',
      input: [restParams({ restSeconds: null })],
    },

    // -- Helpers de série ---------------------------------------------------
    {
      name: 'séries: próximo set_number físico conta aquecimento e ajuste',
      fn: 'nextPhysicalSetNumber',
      input: [[{ set_number: 1 }, { set_number: 2 }, { set_number: 4 }]],
    },
    {
      name: 'séries: "Série N de S" não conta aquecimento nem ajuste',
      fn: 'countNonPreparatorySets',
      input: [[{ set_type: 'warmup' }, { set_type: 'feeler' }, { set_type: null }, { set_type: 'drop_set' }]],
    },
    { name: 'séries: apelido legado normalizado', fn: 'normalizeSetType', input: [' Warm-up '] },
    { name: 'séries: teto seguro só restringe a sugestão', fn: 'clampRpeSuggestion', input: [10, source.MOTOR_CONSTANTS.training_safety.default_max_safe_rpe] },

    // -- Contadores do item (PR B2) -------------------------------------------
    {
      name: 'contadores: avançado com aquecimento gravado e local, ajuste e rest-pause',
      fn: 'buildAdvancedExerciseCounters',
      input: [
        {
          planExercise: COSTAS_AVANCADO,
          loggedSets: [{ set_type: 'warmup' }, { set_type: 'working' }],
          advancedMode: true,
          localProgress: { warmupDone: 2, feelerDone: 0, techniqueSkipped: 0 },
        },
      ],
    },
    {
      name: 'contadores: modo normal zera aquecimento/ajuste e mantém a técnica',
      fn: 'buildAdvancedExerciseCounters',
      input: [{ planExercise: COSTAS_AVANCADO, loggedSets: [{ set_type: 'warmup' }], advancedMode: false, localProgress: null }],
    },
    {
      name: 'contadores: séries sugeridas do par trocado mandam no alvo de trabalho',
      fn: 'buildAdvancedExerciseCounters',
      input: [{ planExercise: TRICEPS, loggedSets: [], advancedMode: false, adjustedSetsCount: 4 }],
    },
    {
      name: 'contadores: sem sets_count e com aquecimento/ajuste sem contagem vale o piso do backend',
      fn: 'buildAdvancedExerciseCounters',
      input: [
        {
          planExercise: { warmup_reps: 12, warmup_sets_count: 0, feeler_set_reps: 6, feeler_set_sets_count: null },
          loggedSets: [{ set_type: 'feeler' }, { set_type: 'feeler' }],
          advancedMode: true,
        },
      ],
    },
    {
      name: 'contadores: aplicação pulada localmente entra na conta',
      fn: 'buildAdvancedExerciseCounters',
      input: [
        {
          planExercise: OMBRO_DROP,
          loggedSets: [{ set_type: 'working' }, { set_type: 'working' }, { set_type: 'working' }, { set_type: 'drop_set' }],
          advancedMode: false,
          localProgress: { warmupDone: 0, feelerDone: 0, techniqueSkipped: 1 },
        },
      ],
    },

    // -- Item coberto por troca em outro slot (PR B2) ------------------------
    {
      name: 'cobertura: exercício da ficha já executado no slot de outro item fica concluído',
      fn: 'findPlanItemsCoveredByAnotherSlotIds',
      input: [{ planExercises: PLAN, loggedExercises: [{ workout_plan_exercise_id: 14, mapping_id: 101, planned_mapping_id: 104 }] }],
    },
    {
      name: 'cobertura: item com lançamento próprio não é coberto',
      fn: 'findPlanItemsCoveredByAnotherSlotIds',
      input: [
        {
          planExercises: PLAN,
          loggedExercises: [
            { workout_plan_exercise_id: 14, mapping_id: 101, planned_mapping_id: 104 },
            { workout_plan_exercise_id: 11, mapping_id: 101, planned_mapping_id: 101 },
          ],
        },
      ],
    },
    {
      name: 'cobertura: lançamento no próprio slot e avulso não cobrem nada',
      fn: 'findPlanItemsCoveredByAnotherSlotIds',
      input: [
        {
          planExercises: PLAN,
          loggedExercises: [
            { workout_plan_exercise_id: 12, mapping_id: 102, planned_mapping_id: 102 },
            { workout_plan_exercise_id: null, mapping_id: 101, planned_mapping_id: null },
          ],
        },
      ],
    },

    // -- Teto de RPE e passo do par (PR B2) -----------------------------------
    {
      name: 'par: prescrição congelada vale sobre o overlay de segurança de hoje',
      fn: 'resolveSessionPairLimits',
      input: [
        {
          planExercise: { ...PEITO, load_increment_kg: 2, prescription: PRESCRIPTION, safety_overlay: { effective_rpe_ceiling: 7 } },
          hasOverride: false,
          pairSuggestion: null,
          syntheticExtra: null,
        },
      ],
    },
    {
      name: 'par: sem prescrição congelada vale o overlay de segurança de hoje',
      fn: 'resolveSessionPairLimits',
      input: [
        {
          planExercise: { ...PEITO, load_increment_kg: 2, safety_overlay: { effective_rpe_ceiling: 7 } },
          hasOverride: false,
          pairSuggestion: null,
          syntheticExtra: null,
        },
      ],
    },
    {
      name: 'par: equipamento trocado lê a sugestão do par novo',
      fn: 'resolveSessionPairLimits',
      input: [
        {
          planExercise: { ...PEITO, prescription: PRESCRIPTION },
          hasOverride: true,
          pairSuggestion: { ...PAIR_SUGGESTION, effective_rpe_ceiling: 9, load_increment_kg: 5 },
          syntheticExtra: null,
        },
      ],
    },
    {
      name: 'par: troca sem a sugestão carregada fica sem teto nem passo',
      fn: 'resolveSessionPairLimits',
      input: [{ planExercise: { ...PEITO, prescription: PRESCRIPTION }, hasOverride: true, pairSuggestion: null, syntheticExtra: null }],
    },
    {
      name: 'par: continuação sintética usa o que a API devolveu na troca',
      fn: 'resolveSessionPairLimits',
      input: [{ planExercise: syntheticItem, hasOverride: false, pairSuggestion: null, syntheticExtra: continuation }],
    },

    // -- Âncora da próxima série (PR B2) --------------------------------------
    {
      name: 'âncora: última série de trabalho, nunca aquecimento nem drop',
      fn: 'findAnchorWorkingSet',
      input: [
        [
          { set_number: 1, set_type: 'warmup', reps: 12, weight_kg: 20 },
          { set_number: 2, set_type: 'working', reps: 10, weight_kg: 60, rpe: 8 },
          { set_number: 3, set_type: null, reps: 9, weight_kg: 60, rpe: 9 },
          { set_number: 4, set_type: 'drop_set', reps: 8, weight_kg: 45 },
        ],
      ],
    },
    {
      name: 'âncora: sem série de trabalho devolve null',
      fn: 'findAnchorWorkingSet',
      input: [[{ set_number: 1, set_type: 'feeler', reps: 3, weight_kg: 50 }]],
    },

    // -- Alvos do par trocado (PR B2) -----------------------------------------
    { name: 'alvos do par: texto aparado e nota presente', fn: 'suggestedProgressionTargets', input: [PAIR_SUGGESTION] },
    {
      name: 'alvos do par: grupo sem trabalho direto (SKIP) vira texto vazio, nunca null',
      fn: 'suggestedProgressionTargets',
      input: [{ ...PAIR_SUGGESTION, reps_target: null, rpe_target: '  ', progression_note: '' }],
    },

    // -- Fluxo e sub-etapa do avançado (PR B3) --------------------------------
    { name: 'fluxo avançado: item com aquecimento/ajuste/técnica no modo avançado', fn: 'hasAdvancedFlow', input: [{ advancedMode: true, planExercise: COSTAS_AVANCADO }] },
    { name: 'fluxo avançado: só aquecimento já basta', fn: 'hasAdvancedFlow', input: [{ advancedMode: true, planExercise: { warmup_reps: 12 } }] },
    { name: 'fluxo avançado: modo normal nunca tem', fn: 'hasAdvancedFlow', input: [{ advancedMode: false, planExercise: COSTAS_AVANCADO }] },
    { name: 'fluxo avançado: item sem configuração avançada não tem', fn: 'hasAdvancedFlow', input: [{ advancedMode: true, planExercise: PEITO }] },
    { name: 'fluxo avançado: sem item não tem', fn: 'hasAdvancedFlow', input: [{ advancedMode: true, planExercise: null }] },

    // -- Modo do treino decidido pela ficha (PR B4) ---------------------------
    { name: 'modo da ficha: sem configuração avançada é normal', fn: 'resolveWorkoutModeFromPlan', input: [[PEITO, TRICEPS]] },
    { name: 'modo da ficha: só aquecimento é avançado', fn: 'resolveWorkoutModeFromPlan', input: [[PEITO, SO_AQUECIMENTO]] },
    { name: 'modo da ficha: só série de ajuste é avançado', fn: 'resolveWorkoutModeFromPlan', input: [[PEITO, SO_AJUSTE]] },
    { name: 'modo da ficha: só técnica é avançado', fn: 'resolveWorkoutModeFromPlan', input: [[PEITO, TRICEPS, OMBRO_DROP]] },
    {
      name: 'modo da ficha: técnica removida pela segurança (resíduo de aplicações) é normal',
      fn: 'resolveWorkoutModeFromPlan',
      input: [[PEITO, TECNICA_REMOVIDA]],
    },
    { name: 'modo da ficha: mistura com um item avançado é avançado', fn: 'resolveWorkoutModeFromPlan', input: [PLAN] },
    { name: 'modo da ficha: ficha vazia é normal', fn: 'resolveWorkoutModeFromPlan', input: [[]] },
    { name: 'modo da ficha: sem ficha é normal', fn: 'resolveWorkoutModeFromPlan', input: [null] },
    { name: 'config avançada: resíduo da técnica removida não conta', fn: 'planHasAdvancedConfig', input: [[TECNICA_REMOVIDA]] },
    { name: 'config avançada: item só com aquecimento conta', fn: 'exerciseHasAdvancedConfig', input: [SO_AQUECIMENTO] },
    { name: 'config avançada: sem item não conta', fn: 'exerciseHasAdvancedConfig', input: [null] },
    {
      name: 'sub-etapa: avançado mostra o aquecimento pendente',
      fn: 'advancedStageOf',
      input: [{ advancedMode: true, planExercise: COSTAS_AVANCADO, counters: counters({ warmupTarget: 2, warmupDone: 1, feelerTarget: 2, techniqueTarget: 3 }) }],
    },
    {
      name: 'sub-etapa: técnica vale também no modo normal (Fase 5)',
      fn: 'advancedStageOf',
      input: [{ advancedMode: false, planExercise: OMBRO_DROP, counters: counters({ loggedNonPreparatory: 3, techniqueTarget: 2 }) }],
    },
    {
      name: 'sub-etapa: modo normal sem técnica é série de trabalho (null)',
      fn: 'advancedStageOf',
      input: [{ advancedMode: false, planExercise: COSTAS_AVANCADO, counters: counters({ warmupTarget: 2 }) }],
    },
    { name: 'sub-etapa: sem item é null', fn: 'advancedStageOf', input: [{ advancedMode: true, planExercise: null, counters: null }] },

    // -- Progresso local das sub-etapas (PR B3) -------------------------------
    {
      name: 'progresso efetivo: o maior entre o local (com puladas) e o gravado',
      fn: 'effectiveAdvancedStageDone',
      input: [{ localProgress: { warmupDone: 1, feelerDone: 2, techniqueSkipped: 0 }, loggedSets: [{ set_type: 'warmup' }, { set_type: 'warmup' }, { set_type: 'feeler' }] }],
    },
    { name: 'progresso efetivo: sem progresso local conta o gravado', fn: 'effectiveAdvancedStageDone', input: [{ localProgress: null, loggedSets: [{ set_type: 'Warm-up' }] }] },
    {
      name: 'progresso local: concluir aquecimento parte do efetivo + 1',
      fn: 'advanceLocalStageProgress',
      input: [{ existing: { warmupDone: 0, feelerDone: 1, techniqueSkipped: 0 }, kind: 'warmup', effectiveDone: { warmupDone: 1, feelerDone: 1 } }],
    },
    {
      name: 'progresso local: pular ajuste sem progresso anterior',
      fn: 'advanceLocalStageProgress',
      input: [{ existing: null, kind: 'feeler', effectiveDone: { warmupDone: 2, feelerDone: 0 } }],
    },
    {
      name: 'progresso local: "Encerrar técnica" marca as restantes como puladas',
      fn: 'advanceLocalStageProgress',
      input: [{ existing: { warmupDone: 2, feelerDone: 2, techniqueSkipped: 1 }, kind: 'technique', effectiveDone: { warmupDone: 2, feelerDone: 2 }, count: 2 }],
    },
    {
      name: '"Encerrar técnica": a aplicação atual e as seguintes',
      fn: 'remainingTechniqueApplications',
      input: [{ kind: 'technique', index: 2, total: 3 }],
    },
    {
      name: '"Encerrar técnica" na última aplicação: só ela',
      fn: 'remainingTechniqueApplications',
      input: [{ kind: 'technique', index: 3, total: 3 }],
    },

    // -- Linha "Última": séries de trabalho da sessão anterior (PR B3) --------
    {
      name: 'sessão anterior: só trabalho, sem aplicação de técnica, em ordem de execução',
      fn: 'selectPreviousSessionWorkingSets',
      input: [
        {
          item: {
            sets: [
              { set_number: 5, set_type: 'working', reps: 8, weight_kg: 60, rpe: 9 },
              { set_number: 1, set_type: 'warmup', reps: 12, weight_kg: 20, rpe: null },
              { set_number: 3, set_type: null, reps: 10, weight_kg: 60, rpe: 8 },
              { set_number: 2, set_type: 'feeler', reps: 3, weight_kg: 50, rpe: null },
              { set_number: 6, set_type: 'working', reps: 6, weight_kg: 60, rpe: 10, is_technique_application: true },
              { set_number: 7, set_type: 'backoff', reps: 12, weight_kg: 45, rpe: 8 },
              { set_number: 4, set_type: 'drop_set', reps: 6, weight_kg: 45, rpe: 10 },
            ],
          },
        },
      ],
    },
    { name: 'sessão anterior: sem histórico devolve lista vazia', fn: 'selectPreviousSessionWorkingSets', input: [{ item: null }] },

    // -- Medidas de série (Lote T5) -------------------------------------------
    { name: 'medida: item sem medida é repetições', fn: 'setMeasureOf', input: [PEITO] },
    { name: 'medida: item por tempo com o alvo', fn: 'setMeasureOf', input: [PRANCHA] },
    {
      name: 'medida: prescrição congelada vale sobre a linha da ficha',
      fn: 'setMeasureOf',
      input: [{ ...TRICEPS, prescription: { measure: MEASURES.max_reps, time_window_seconds: 30 } }],
    },
    { name: 'medida: leitura tolerante (desconhecida = reps)', fn: 'normalizeMeasure', input: [' Xyz '] },
    { name: 'medida: tempo não é baseada em reps', fn: 'isRepBased', input: [MEASURES.duration] },
    {
      name: 'registro reps: IA > última série > alvo > fallback, carga obrigatória',
      fn: 'measureLoggerDefaults',
      input: [{ measure: null, repsTarget: '8-12', lastSet: { reps: 9, weight_kg: 60 } }],
    },
    {
      name: 'registro reps: sem histórico cai no fallback do backend',
      fn: 'measureLoggerDefaults',
      input: [{ measure: MEASURES.reps, repsTarget: null, lastSet: null }],
    },
    {
      name: 'registro tempo: segundos do alvo, sem reps, carga opcional',
      fn: 'measureLoggerDefaults',
      input: [{ measure: MEASURES.duration, durationSecondsTarget: PRANCHA.duration_seconds_target, suggestedReps: 10 }],
    },
    {
      name: 'registro tempo: segundos da última série desta sessão',
      fn: 'measureLoggerDefaults',
      input: [{ measure: MEASURES.duration, durationSecondsTarget: 60, lastSet: { reps: null, duration_seconds: 52 } }],
    },
    {
      name: 'registro máximo: reps obrigatórias sem alvo, janela do item',
      fn: 'measureLoggerDefaults',
      input: [{ measure: MEASURES.max_reps, timeWindowSeconds: 30, repsTarget: '8-12', lastSet: null }],
    },
    { name: 'cronômetro: reps não tem', fn: 'executionTimerSpec', input: [{ measure: MEASURES.reps, durationSecondsTarget: 60 }] },
    {
      name: 'cronômetro: tempo é regressivo até o alvo',
      fn: 'executionTimerSpec',
      input: [{ measure: MEASURES.duration, durationSecondsTarget: 60 }],
    },
    {
      name: 'cronômetro: máximo com janela é regressivo da janela',
      fn: 'executionTimerSpec',
      input: [{ measure: MEASURES.max_reps, timeWindowSeconds: 30 }],
    },
    { name: 'cronômetro: máximo sem janela é livre', fn: 'executionTimerSpec', input: [{ measure: MEASURES.max_reps }] },
    {
      name: 'rótulo: série por tempo com o alvo',
      fn: 'measureSetLabel',
      input: ['Série 1 de 3', { measure: MEASURES.duration, durationSecondsTarget: 60 }],
    },
    { name: 'rótulo: série de reps fica como veio', fn: 'measureSetLabel', input: ['Série 1 de 3', { measure: null }] },
    {
      name: 'alvo: máximo com janela',
      fn: 'measureTargetText',
      input: [{ measure: MEASURES.max_reps, timeWindowSeconds: 30 }],
    },
    {
      name: 'texto da série: máximo em janela (espelho do dono)',
      fn: 'formatSetMeasureText',
      input: [{ measure: MEASURES.max_reps, reps: 15, durationSeconds: 30 }],
    },
    {
      name: 'POST da série: reps não ganha chave nenhuma',
      fn: 'setMeasurePayload',
      input: [{ reps: 10, weight_kg: 40, rpe: 8 }],
    },
    {
      name: 'POST da série: tempo manda a medida e os segundos',
      fn: 'setMeasurePayload',
      input: [{ reps: null, weight_kg: null, measure: MEASURES.duration, duration_seconds: 58 }],
    },
    {
      name: 'segundos: acima do teto do dono não valem',
      fn: 'isDurationSecondsValid',
      input: [MEASURE_LIMITS.duration_seconds_max + 1],
    },
    {
      name: 'máximo de reps: abaixo do piso do dono não vale',
      fn: 'isMaxRepsValid',
      input: [MEASURE_LIMITS.max_reps_min_reps - 1],
    },
    {
      name: 'modo da ficha: item por tempo nunca é avançado, nem com resíduo',
      fn: 'resolveWorkoutModeFromPlan',
      input: [[PEITO, PRANCHA_RESIDUO, MAXIMO_JANELA]],
    },
    {
      name: 'fluxo avançado: item por tempo no modo avançado não tem',
      fn: 'hasAdvancedFlow',
      input: [{ advancedMode: true, planExercise: PRANCHA_RESIDUO }],
    },
    {
      name: 'aquecimento genérico: item por tempo não pede',
      fn: 'shouldShowGenericWarmup',
      input: [
        {
          advancedMode: false,
          measure: MEASURES.duration,
          hasCurrent: true,
          loggedCount: 0,
          workoutPlanExerciseId: PRANCHA.workout_plan_exercise_id,
          needsWarmup: true,
          warmupState: { first: 'pending', second: 'pending' },
        },
      ],
    },
    {
      name: 'próxima série: por tempo, só o rótulo, sem sugestão de carga',
      fn: 'resolveNextSetPreview',
      input: [
        {
          isExerciseTransition: false,
          stage: null,
          workingSetNumber: 2,
          workingSetTotal: 3,
          weightKg: 20,
          measure: MEASURES.duration,
        },
      ],
    },
    {
      name: 'troca depois da 1ª série: continuação de item por tempo continua por tempo',
      fn: 'buildSyntheticContinuation',
      input: [{ localId: -1, current: PRANCHA, remainingSets: 2, choice: SWAP_CHOICE, targets: SWAP_TARGETS, existingExtras: [] }],
    },

    // -- Piso de séries da continuação sintética (PR B3) ----------------------
    {
      name: 'troca depois da última série prevista: continuação fica com o piso MIN_SETS_COUNT',
      fn: 'buildSyntheticContinuation',
      input: [{ localId: -1, current: COSTAS_AVANCADO, remainingSets: 0, choice: SWAP_CHOICE, targets: SWAP_TARGETS, existingExtras: [] }],
    },
  ];
}

// Gerado por este teste (UPDATE_GOLDEN=1); não edite à mão. Sem `_comment`:
// o contrato com o repositório Zepp é um array puro de casos.
function generateGolden(core: CoreModule): GoldenCase[] {
  return caseDefinitions().map((definition) => {
    const input = JSON.parse(JSON.stringify(definition.input)) as unknown[];
    return { ...definition, input, expected: call(core, { fn: definition.fn, input }) };
  });
}

// ---------------------------------------------------------------------------

describe('bundle do núcleo do Modo Treino', () => {
  it('compila em um ES module e exporta a API pura', () => {
    expect(bundleBytes).toBeGreaterThan(0);
    expect(typeof bundle.buildExecutionQueue).toBe('function');
    expect(typeof bundle.reorderPendingBySkipByPlanItem).toBe('function');
    expect(Object.keys(bundle).sort()).toEqual(Object.keys(source).sort());
  });

  it.each(FORBIDDEN_GLOBALS)('não cita `%s`', (name) => {
    expect(bundleCode).not.toMatch(new RegExp(`\\b${name}\\b`));
  });

  it('constantes vêm do backend (motor-constants.generated.json)', () => {
    expect(bundle.GENERIC_WARMUP_REPS).toBe(motorConstants.session_loads.generic_warmup_reps);
    expect(bundle.MOTOR_CONSTANTS).toEqual(motorConstants);
    expect(bundle.DEFAULT_REST_SECONDS).toBe(motorConstants.prescription.default_rest_seconds);
    expect(bundle.MIN_SETS_COUNT).toBe(motorConstants.prescription.min_sets_count);
    expect(bundle.SET_LOGGER_FALLBACK_REPS).toBe(motorConstants.prescription.set_logger_fallback_reps);
    expect(bundle.SET_LOGGER_FALLBACK_LOAD_KG).toBe(motorConstants.prescription.set_logger_fallback_load_kg);
  });

  it('exporta a orquestração que antes só existia na tela (PR B2)', () => {
    for (const name of [
      'buildAdvancedExerciseCounters',
      'findAnchorWorkingSet',
      'findPlanItemsCoveredByAnotherSlot',
      'findPlanItemsCoveredByAnotherSlotIds',
      'resolveSessionPairLimits',
      'suggestedProgressionTargets',
      // PR B3
      'advanceLocalStageProgress',
      'advancedStageOf',
      'effectiveAdvancedStageDone',
      'hasAdvancedFlow',
      'remainingTechniqueApplications',
      'selectPreviousSessionWorkingSets',
      // PR B4
      'exerciseHasAdvancedConfig',
      'planHasAdvancedConfig',
      'resolveWorkoutModeFromPlan',
      // Lote T5 (medidas de série)
      'executionTimerSpec',
      'formatSetMeasureText',
      'isDurationSecondsValid',
      'isMaxRepsValid',
      'isRepBased',
      'measureLoggerDefaults',
      'measureSetLabel',
      'measureTargetText',
      'normalizeMeasure',
      'setMeasureOf',
      'setMeasurePayload',
    ]) {
      expect(typeof (bundle as unknown as Record<string, unknown>)[name], name).toBe('function');
    }
  });
});

describe('golden como módulo JS (workout-mode-core.golden.js, PR B2)', () => {
  it('o módulo gerado no build é o MESMO array do golden JSON', async () => {
    const { goldenCode } = await buildWorkoutCore();
    expect(goldenCode).toBe(goldenModuleSource());
    expect(goldenCode).toMatch(/^\/\/ GERADO[^\n]*\nexport default \[/);
    const file = path.join(tempDir, 'workout-mode-core.golden.mjs');
    writeFileSync(file, goldenCode);
    const loaded = (await import(/* @vite-ignore */ pathToFileURL(file).href)) as { default: GoldenCase[] };
    expect(loaded.default).toEqual(JSON.parse(readFileSync(GOLDEN_PATH, 'utf8')));
  });

  it('recusa golden que não é array', () => {
    expect(() => goldenModuleSource('{"cases": []}')).toThrow(/array/);
  });
});

describe('bundle: paridade com session_loads.py (session-loads.parity.json)', () => {
  it.each(parity.cases.map((c) => [`âncora ${c.anchor_kg} kg, passo ${c.load_increment_kg ?? 'default'}`, c] as const))(
    '%s',
    (_name, c) => {
      const step = c.load_increment_kg;
      expect(bundle.roundToStep(c.anchor_kg, step)).toBe(c.round_to_step);
      expect(bundle.mirrorWarmupLoads(c.anchor_kg, step)).toEqual(c.warmup_loads_kg);
      expect(c.feeler_loads_kg.map((_, i) => bundle.mirrorFeelerLoad(c.anchor_kg, i + 1, step))).toEqual(c.feeler_loads_kg);
      expect(c.drop_set_loads_kg.map((_, i) => bundle.mirrorTechniqueApplicationLoad('drop_set', c.anchor_kg, i + 1, step))).toEqual(
        c.drop_set_loads_kg,
      );
      expect(
        c.rest_pause_loads_kg.map((_, i) => bundle.mirrorTechniqueApplicationLoad('rest_pause', c.anchor_kg, i + 1, step)),
      ).toEqual(c.rest_pause_loads_kg);
    },
  );
});

describe('golden do núcleo (workout-mode-core.golden.json)', () => {
  it('o golden commitado é o que o núcleo produz hoje', () => {
    const generated = generateGolden(bundle);
    if (process.env.UPDATE_GOLDEN === '1') {
      writeFileSync(GOLDEN_PATH, `${JSON.stringify(generated, null, 2)}\n`);
    }
    expect(existsSync(GOLDEN_PATH), 'golden ausente: rode com UPDATE_GOLDEN=1').toBe(true);
    const committed = JSON.parse(readFileSync(GOLDEN_PATH, 'utf8')) as GoldenCase[];
    expect(Array.isArray(committed)).toBe(true);
    expect(committed).toEqual(generated);
  });

  it('cada caso roda igual no bundle e no código-fonte (o celular)', () => {
    const golden = JSON.parse(readFileSync(GOLDEN_PATH, 'utf8')) as GoldenCase[];
    const names = new Set<string>();
    for (const testCase of golden) {
      expect(names.has(testCase.name), `nome repetido: ${testCase.name}`).toBe(false);
      names.add(testCase.name);
      expect(call(bundle, testCase), testCase.name).toEqual(testCase.expected);
      expect(call(source, testCase), testCase.name).toEqual(testCase.expected);
    }
  });
});

describe('adaptadores JSON da fila = funções originais com Set/callback (como a tela chama)', () => {
  const items = [
    { done: true, planExercise: PEITO },
    { done: false, planExercise: TRICEPS },
    { done: false, planExercise: OMBRO_DROP },
    { done: false, planExercise: COSTAS_AVANCADO },
  ];

  it('reorderPendingBySkipByPlanItem', () => {
    const keyOf = (item: (typeof items)[number]) => item.planExercise.workout_plan_exercise_id ?? -1;
    expect(source.reorderPendingBySkipByPlanItem(items, [12], 14)).toEqual(
      source.reorderPendingBySkip(items, keyOf, new Set([12]), 14),
    );
  });

  it('buildExecutionQueueFromIds', () => {
    expect(source.buildExecutionQueueFromIds({ planExercises: PLAN, syntheticExercises: [], cancelledIds: [13] })).toEqual(
      source.buildExecutionQueue({ planExercises: PLAN, syntheticExercises: [], cancelledIds: new Set([13]) }),
    );
  });

  it('findPlanItemForSubstitutedExerciseFromIds', () => {
    const params = { planExercises: PLAN, chosenMappingId: 102, currentWorkoutPlanExerciseId: 11 };
    expect(source.findPlanItemForSubstitutedExerciseFromIds({ ...params, pendingIds: [12] })).toBe(
      source.findPlanItemForSubstitutedExercise({ ...params, isPending: (id) => id === 12 }),
    );
  });
});
