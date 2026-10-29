import { afterEach, describe, expect, it } from 'vitest';

import { resetAdvancedTechniqueCatalog, setAdvancedTechniqueCatalog, FALLBACK_ADVANCED_TECHNIQUES } from './advanced-techniques';
import { techniqueCatalogFromItems } from './technique-accounting';
import { isAdvancedExerciseDone, resolveAdvancedSubStage, type AdvancedExerciseCounters } from './workout-mode-plan-flow';
import {
  blockApplicationRows,
  countWorkingSets,
  describeAppliedRows,
  findAnchorWorkingSet,
  previewTechniqueApplication,
  techniqueGoalLabel,
  techniqueStageLabel,
  techniqueStageTarget,
  toTechniqueApplication,
  type TechniqueSetRow,
} from './workout-mode-technique';

// Catálogo local = seed 031 + coeficientes da 060 (mesmos da API).
const CATALOG = techniqueCatalogFromItems(FALLBACK_ADVANCED_TECHNIQUES);

afterEach(() => resetAdvancedTechniqueCatalog());

describe('techniqueStageTarget: só rest-pause/drop-set abrem sub-etapa', () => {
  it.each<[string | null, number | null, number]>([
    ['rest_pause', 3, 3],
    ['drop_set', 2, 2],
    ['fst7', 7, 0],
    ['gvt', 10, 0],
    ['forced_reps', 3, 0],
    ['negative_reps', 2, 0],
    [null, 3, 0],
    ['rest_pause', null, 0],
  ])('%s × %s -> %s', (code, applications, esperado) => {
    expect(techniqueStageTarget({ advanced_technique: code, advanced_technique_applications: applications })).toBe(esperado);
  });

  it('segue o set_type do catálogo carregado da API', () => {
    setAdvancedTechniqueCatalog([
      ...FALLBACK_ADVANCED_TECHNIQUES,
      { ...FALLBACK_ADVANCED_TECHNIQUES[0], code: 'myo_reps', name: 'Myo-reps', set_type: 'rest_pause' },
    ]);
    expect(techniqueStageTarget({ advanced_technique: 'myo_reps', advanced_technique_applications: 4 })).toBe(4);
  });
});

const SETS: TechniqueSetRow[] = [
  { set_number: 1, set_type: 'warmup', reps: 15, weight_kg: 10 },
  { set_number: 2, set_type: 'working', reps: 12, weight_kg: 50, rpe: 9 },
  { set_number: 3, set_type: 'working', reps: 12, weight_kg: '50.00', rpe: '10.0' },
  { set_number: 4, set_type: 'rest_pause', reps: 6, weight_kg: 50, rpe: 10, technique_code: 'rest_pause', anchor_set_number: 3 },
  // linha antiga/Zepp sem vínculo: posterior à âncora -> pertence ao bloco
  { set_number: 5, set_type: 'rest_pause', reps: 4, weight_kg: 50, rpe: null },
];

describe('âncora e bloco', () => {
  it('âncora é a última série de trabalho; "Série N de S" conta só trabalho', () => {
    expect(findAnchorWorkingSet(SETS)?.set_number).toBe(3);
    expect(countWorkingSets(SETS)).toBe(2);
  });

  it('bloco = vínculo explícito ou linha sem vínculo posterior à âncora', () => {
    expect(blockApplicationRows(SETS, 3).map((row) => row.set_number)).toEqual([4, 5]);
    expect(blockApplicationRows(SETS, 2).map((row) => row.set_number)).toEqual([]);
    expect(blockApplicationRows(SETS, null)).toEqual([]);
  });

  it('aplicação sem technique_code usa a técnica da ficha', () => {
    expect(toTechniqueApplication(SETS[4], 'rest_pause')).toEqual({ techniqueCode: 'rest_pause', reps: 4, rpe: null });
  });
});

describe('previewTechniqueApplication (roteiro de DEV: 12 a RPE 10 + rest-pause 6 e 6)', () => {
  const anchor: TechniqueSetRow = { set_number: 3, set_type: 'working', reps: 12, rpe: 10, weight_kg: 50 };

  it('1º mini-bloco de 6 = 0,5 · bloco 0,5/1,0', () => {
    expect(
      previewTechniqueApplication({ catalog: CATALOG, techniqueCode: 'rest_pause', anchor, previousApplications: [], reps: 6, rpe: 10 }),
    ).toEqual({ application: 0.5, block: 0.5, cap: 1, capped: false });
  });

  it('2º mini-bloco de 6 = 0,5 · bloco 1,0/1,0', () => {
    const preview = previewTechniqueApplication({
      catalog: CATALOG,
      techniqueCode: 'rest_pause',
      anchor,
      previousApplications: [{ techniqueCode: 'rest_pause', reps: 6, rpe: 10 }],
      reps: 6,
      rpe: 10,
    });
    expect(preview).toEqual({ application: 0.5, block: 1, cap: 1, capped: false });
  });

  it('passa do teto: bloco fica em 1,0 e sinaliza', () => {
    const preview = previewTechniqueApplication({
      catalog: CATALOG,
      techniqueCode: 'rest_pause',
      anchor,
      previousApplications: [
        { techniqueCode: 'rest_pause', reps: 6, rpe: 10 },
        { techniqueCode: 'rest_pause', reps: 4, rpe: 10 },
      ],
      reps: 3,
      rpe: 10,
    });
    expect(preview?.block).toBe(1);
    expect(preview?.capped).toBe(true);
  });

  it('âncora sem RPE: referência 10; RPE 8 pondera pelo esforço', () => {
    const preview = previewTechniqueApplication({
      catalog: CATALOG,
      techniqueCode: 'drop_set',
      anchor: { ...anchor, rpe: null },
      previousApplications: [],
      reps: 6,
      rpe: 8,
    });
    expect(preview?.application).toBeCloseTo((0.5 * 0.6) / 1.05, 9);
  });

  it('FST-7 (teto 0) não tem feedback', () => {
    expect(
      previewTechniqueApplication({ catalog: CATALOG, techniqueCode: 'fst7', anchor, previousApplications: [], reps: 6, rpe: 10 }),
    ).toBeNull();
  });
});

describe('rótulos', () => {
  it('meta é "até a falha (RPE X)", nunca as reps da série', () => {
    expect(techniqueGoalLabel(10)).toBe('até a falha (RPE 10)');
    expect(techniqueGoalLabel('9.5')).toBe('até a falha (RPE 9,5)');
    expect(techniqueGoalLabel(null)).toBe('até a falha (RPE 10)');
  });

  it('etapa: "Rest-pause · mini-bloco 1/3" e "Drop 2/2"', () => {
    expect(techniqueStageLabel('rest_pause', 'Rest-pause', 1, 3)).toBe('Rest-pause · mini-bloco 1/3');
    expect(techniqueStageLabel('drop_set', 'Drop-set', 2, 2)).toBe('Drop 2/2');
  });

  it('subitens das aplicações já lançadas', () => {
    expect(describeAppliedRows('rest_pause', blockApplicationRows(SETS, 3))).toEqual([
      'Mini-bloco 1: 6 reps · RPE 10',
      'Mini-bloco 2: 4 reps',
    ]);
  });
});

describe('fluxo nos dois modos: técnica depois da última série de trabalho', () => {
  const counters = (partial: Partial<AdvancedExerciseCounters>): AdvancedExerciseCounters => ({
    workingTarget: 3,
    loggedNonPreparatory: 0,
    warmupTarget: 0,
    warmupDone: 0,
    feelerTarget: 0,
    feelerDone: 0,
    techniqueTarget: techniqueStageTarget({ advanced_technique: 'rest_pause', advanced_technique_applications: 2 }),
    techniqueSkipped: 0,
    ...partial,
  });

  it('modo normal (sem aquecimento/ajuste): trabalho, depois mini-blocos', () => {
    expect(resolveAdvancedSubStage(counters({ loggedNonPreparatory: 2 }))).toBeNull();
    expect(resolveAdvancedSubStage(counters({ loggedNonPreparatory: 3 }))).toEqual({ kind: 'technique', index: 1, total: 2 });
    expect(isAdvancedExerciseDone(counters({ loggedNonPreparatory: 3 }))).toBe(false);
    expect(isAdvancedExerciseDone(counters({ loggedNonPreparatory: 5 }))).toBe(true);
  });

  it('"Encerrar técnica" no 1º mini-bloco (pula os restantes) conclui o exercício', () => {
    expect(isAdvancedExerciseDone(counters({ loggedNonPreparatory: 3, techniqueSkipped: 2 }))).toBe(true);
    expect(isAdvancedExerciseDone(counters({ loggedNonPreparatory: 4, techniqueSkipped: 1 }))).toBe(true);
  });
});
