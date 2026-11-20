import { MantineProvider } from '@mantine/core';
import { createElement, type ReactElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import motorConstants from '@/lib/motor-constants.generated.json';
import {
  commitRpeDraft,
  fitRpeTargets,
  isOnRpeStep,
  isValidRpeTarget,
  RPE_TARGET_SCALE,
  sanitizeRpeDraft,
  stepDecimalScale,
} from '@/lib/workout-plan-template-form';
import { theme } from '@/theme';
import { RpePerSetField } from './RpePerSetField';

function render(element: ReactElement): string {
  return renderToStaticMarkup(createElement(MantineProvider, { theme }, element)).replace(/<style[^>]*>[\s\S]*?<\/style>/g, '');
}

const noop = () => {};

describe('RpePerSetField — renderização', () => {
  it('alvo gravado sem lista: texto literal e ação "Definir por série"', () => {
    const html = render(createElement(RpePerSetField, { targets: [], setsCount: 3, onCommit: noop, storedText: '8-9' }));
    expect(html).toContain('Alvo gravado: 8-9');
    expect(html).toContain('Definir por série');
    expect(html).not.toContain('RPE esperado da série 1');
  });

  it('com lista: um campo por série e a dica do objetivo', () => {
    const html = render(
      createElement(RpePerSetField, { targets: [7, 8, 9], setsCount: 3, onCommit: noop, hint: 'ex.: 8' }),
    );
    for (const n of [1, 2, 3]) expect(html).toContain(`RPE esperado da série ${n}`);
    expect(html).not.toContain('RPE esperado da série 4');
    expect(html).toContain('ex.: 8');
    expect(html).toContain('Repetir a 1ª em todas');
  });
});

describe('passo do RPE (espelho)', () => {
  it('casas decimais derivadas do passo', () => {
    expect(stepDecimalScale(0.5)).toBe(1);
    expect(stepDecimalScale(1)).toBe(0);
  });

  it('só números no passo do espelho passam; texto intermediário passa', () => {
    const { step } = RPE_TARGET_SCALE;
    expect(isOnRpeStep(8 + step)).toBe(true);
    expect(isOnRpeStep(8 + step / 5)).toBe(false);
    expect(isOnRpeStep('8.')).toBe(true);
    expect(isOnRpeStep(8.5, 0.5)).toBe(true);
    expect(isOnRpeStep(8.3, 0.5)).toBe(false);
  });
});

describe('RPE-alvo válido (escala + passo do espelho)', () => {
  const { min, max, step } = RPE_TARGET_SCALE;

  it('limites e valores no passo são válidos; fora da escala ou do passo, não', () => {
    expect(isValidRpeTarget(min)).toBe(true);
    expect(isValidRpeTarget(max)).toBe(true);
    expect(isValidRpeTarget(min + step)).toBe(true);
    expect(isValidRpeTarget(max + step)).toBe(false);
    expect(isValidRpeTarget(min - step)).toBe(false);
    expect(isValidRpeTarget(0)).toBe(false);
    expect(isValidRpeTarget(max - step / 5)).toBe(false);
    expect(isValidRpeTarget('8.')).toBe(true);
  });

  it('commit: rascunho fora da escala volta ao último valor válido e nunca chega ao onCommit', () => {
    const last = [8, 9];
    expect(commitRpeDraft(sanitizeRpeDraft([max + 5, '0'], last))).toEqual([8, 9]);
    expect(commitRpeDraft(sanitizeRpeDraft([`${max + 5}.`, ''], last))).toEqual([8, 8]);
    expect(commitRpeDraft(sanitizeRpeDraft([max + 5, ''], []))).toEqual([]);
    expect(commitRpeDraft(sanitizeRpeDraft([7, max], last))).toEqual([7, max]);
  });
});

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
