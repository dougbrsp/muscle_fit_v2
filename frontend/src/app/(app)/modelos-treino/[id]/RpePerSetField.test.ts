import { describe, expect, it } from 'vitest';
import motorConstants from '@/lib/motor-constants.generated.json';
import { commitRpeDraft, fitRpeTargets, RPE_TARGET_SCALE } from '@/lib/workout-plan-template-form';

describe('RPE esperado por série — lista do editor (#146)', () => {
  it('limites e passo vêm do espelho gerado', () => {
    expect(RPE_TARGET_SCALE).toEqual(motorConstants.rpe_target_scale);
  });

  it('tudo em branco = sem alvo', () => {
    expect(commitRpeDraft(['', '', ''])).toEqual([]);
    expect(commitRpeDraft([null, undefined])).toEqual([]);
  });

  it('aplicar a todas: valor copiado em todas as séries', () => {
    expect(commitRpeDraft([8, 8, 8, 8])).toEqual([8, 8, 8, 8]);
  });

  it('edição individual preserva os demais valores', () => {
    expect(commitRpeDraft([7, 8, 9])).toEqual([7, 8, 9]);
    expect(commitRpeDraft([7, '9', 9])).toEqual([7, 9, 9]);
  });

  it('lista parcial: branco recebe o valor anterior; branco inicial recebe o 1º preenchido', () => {
    expect(commitRpeDraft([7, '', 9, ''])).toEqual([7, 7, 9, 9]);
    expect(commitRpeDraft(['', '', 8.5])).toEqual([8.5, 8.5, 8.5]);
  });

  it('aumentar séries: nova nasce com o valor da última; nada digitado se perde', () => {
    expect(fitRpeTargets([7, 8], 4)).toEqual([7, 8, 8, 8]);
  });

  it('diminuir séries: sai do fim', () => {
    expect(fitRpeTargets([7, 8, 9, 9], 2)).toEqual([7, 8]);
  });

  it('sem alvo continua sem alvo ao mudar as séries', () => {
    expect(fitRpeTargets([], 5)).toEqual([]);
  });
});
