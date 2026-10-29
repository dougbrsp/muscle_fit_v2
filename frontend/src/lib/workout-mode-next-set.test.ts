import { describe, expect, it } from 'vitest';

import { feelerSetLoad, warmupLoad } from './workout-mode-load-suggestions';
import { resolveNextSetPreview } from './workout-mode-next-set';
import { resolveAdvancedSubStage, type AdvancedExerciseCounters } from './workout-mode-plan-flow';

const BASE = {
  isExerciseTransition: false,
  stage: null,
  workingSetNumber: 2,
  workingSetTotal: 3,
  weightKg: 70,
} as const;

describe('resolveNextSetPreview — bordas que nao podem mostrar numero errado', () => {
  it('transicao de exercicio nao tem "proxima serie deste exercicio"', () => {
    expect(resolveNextSetPreview({ ...BASE, isExerciseTransition: true })).toBeNull();
  });

  it('sem carga resolvida nao inventa valor', () => {
    expect(resolveNextSetPreview({ ...BASE, weightKg: null })).toBeNull();
    expect(resolveNextSetPreview({ ...BASE, weightKg: Number.NaN })).toBeNull();
  });

  it('serie de trabalho usa o mesmo rotulo do card de registro', () => {
    expect(resolveNextSetPreview(BASE)).toEqual({ label: 'Série 2 de 3', weightKg: 70 });
  });

  it('acima da meta some com o "de N" em vez de mentir', () => {
    expect(resolveNextSetPreview({ ...BASE, workingSetNumber: 4, workingSetTotal: 3 })?.label).toBe('Série 4');
  });

  it('carga 0 (peso corporal/maquina sem carga) continua sendo um valor valido', () => {
    expect(resolveNextSetPreview({ ...BASE, weightKg: 0 })?.weightKg).toBe(0);
  });

  it('aplicacao de tecnica sem substantivo cai num rotulo generico, nunca num indice adivinhado', () => {
    const preview = resolveNextSetPreview({
      ...BASE,
      stage: { kind: 'technique', index: 2, total: 3 },
      techniqueApplicationNoun: '  ',
    });
    expect(preview).toEqual({ label: 'Próxima série', weightKg: 70 });
  });
});

// A transicao aquecimento -> ajuste -> trabalho vem de `resolveAdvancedSubStage`
// (mesma funcao que decide a sub-etapa do card). Aqui provamos a cadeia inteira:
// a carga anunciada no descanso e a que o card vai oferecer depois.
describe('cadeia real: sub-etapa do modo avancado x carga anunciada no descanso', () => {
  const REFERENCE_WORKING_LOAD = 100;

  function counters(partial: Partial<AdvancedExerciseCounters>): AdvancedExerciseCounters {
    return {
      workingTarget: 3,
      loggedNonPreparatory: 0,
      warmupTarget: 2,
      warmupDone: 0,
      feelerTarget: 2,
      feelerDone: 0,
      techniqueTarget: 0,
      techniqueSkipped: 0,
      ...partial,
    };
  }

  function previewAfter(partial: Partial<AdvancedExerciseCounters>) {
    const stage = resolveAdvancedSubStage(counters(partial));
    const weightKg =
      stage?.kind === 'warmup'
        ? warmupLoad(REFERENCE_WORKING_LOAD)
        : stage?.kind === 'feeler'
          ? feelerSetLoad(REFERENCE_WORKING_LOAD, stage.index)
          : REFERENCE_WORKING_LOAD;
    return resolveNextSetPreview({
      isExerciseTransition: false,
      stage,
      workingSetNumber: (partial.loggedNonPreparatory ?? 0) + 1,
      workingSetTotal: 3,
      weightKg,
    });
  }

  it('1o aquecimento -> 2o aquecimento (20% fixo, nao progride com o indice)', () => {
    expect(previewAfter({ warmupDone: 0 })).toEqual({ label: 'Aquecimento 1/2', weightKg: 20 });
    expect(previewAfter({ warmupDone: 1 })).toEqual({ label: 'Aquecimento 2/2', weightKg: 20 });
  });

  it('acabou o aquecimento -> serie de ajuste 1 (50%) e depois a 2 (70%)', () => {
    expect(previewAfter({ warmupDone: 2 })).toEqual({ label: 'Série de ajuste 1/2', weightKg: 50 });
    expect(previewAfter({ warmupDone: 2, feelerDone: 1 })).toEqual({ label: 'Série de ajuste 2/2', weightKg: 70 });
  });

  it('acabou o ajuste -> serie de trabalho com a carga de trabalho cheia', () => {
    expect(previewAfter({ warmupDone: 2, feelerDone: 2 })).toEqual({ label: 'Série 1 de 3', weightKg: 100 });
    expect(previewAfter({ warmupDone: 2, feelerDone: 2, loggedNonPreparatory: 2 })).toEqual({
      label: 'Série 3 de 3',
      weightKg: 100,
    });
  });

  it('ultima serie de trabalho: quem manda e a transicao de exercicio, nao o contador', () => {
    const stage = resolveAdvancedSubStage(counters({ warmupDone: 2, feelerDone: 2, loggedNonPreparatory: 3 }));
    expect(stage).toBeNull();
    expect(
      resolveNextSetPreview({
        isExerciseTransition: true,
        stage,
        workingSetNumber: 4,
        workingSetTotal: 3,
        weightKg: 100,
      }),
    ).toBeNull();
  });

  it('com tecnica configurada, a proxima apos a ultima serie de trabalho e a 1a aplicacao', () => {
    const stage = resolveAdvancedSubStage(
      counters({ warmupDone: 2, feelerDone: 2, loggedNonPreparatory: 3, techniqueTarget: 3 }),
    );
    expect(
      resolveNextSetPreview({
        isExerciseTransition: false,
        stage,
        techniqueApplicationNoun: 'Drop',
        workingSetNumber: 4,
        workingSetTotal: 3,
        weightKg: 80,
      }),
    ).toEqual({ label: 'Drop 1/3', weightKg: 80 });
  });
});
