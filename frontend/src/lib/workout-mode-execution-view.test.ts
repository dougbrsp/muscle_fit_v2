import { describe, expect, it } from 'vitest';
import { createSubmitLatch } from './submit-latch';
import { aiTipView, expectedRpeLine, fieldsToRefresh, initialRpe, isLongText, setTargetLine } from './workout-mode-execution-view';
import type { SetEvaluationResult } from './workout-mode-types';

const RESULT: SetEvaluationResult = {
  model: 'test',
  category: 'HOLD',
  category_label: 'Manter',
  note: 'Foque na descida controlada.',
  raw_text: '',
};

describe('aiTipView', () => {
  it('só com o toggle ligado e texto real da IA', () => {
    expect(aiTipView(true, RESULT)).toEqual({ note: 'Foque na descida controlada.', realignedByEngine: false });
    expect(aiTipView(false, RESULT)).toBeNull();
    expect(aiTipView(true, null)).toBeNull();
    expect(aiTipView(true, { ...RESULT, note: '' })).toBeNull();
  });

  it('category_from_ai presente: a categoria é do motor, e o card diz isso', () => {
    expect(aiTipView(true, { ...RESULT, category_from_ai: 'INCREASE_LOAD' })?.realignedByEngine).toBe(true);
  });
});

describe('setTargetLine', () => {
  it('só campos existentes', () => {
    expect(setTargetLine({ measured: false, repsTarget: '10-12', loadText: '15 kg', rpeTarget: '9' })).toBe(
      'Alvo: 10-12 reps · 15 kg · RPE 9',
    );
    expect(setTargetLine({ measured: false, repsTarget: '8', loadText: null, rpeTarget: null })).toBe('Alvo: 8 reps');
    expect(setTargetLine({ measured: false })).toBeNull();
  });

  it('série medida usa o alvo da medida', () => {
    expect(setTargetLine({ measured: true, measureTarget: '60 s', rpeTarget: '8' })).toBe('Alvo: 60 s · RPE 8');
    expect(setTargetLine({ measured: true, measureTarget: null })).toBe('Alvo: -');
  });
});

describe('fieldsToRefresh', () => {
  it('mesma série: campo editado à mão não é sobrescrito', () => {
    expect([...fieldsToRefresh(false, new Set(['weight']))].sort()).toEqual(['duration', 'reps', 'rpe']);
  });

  it('série nova: tudo volta ao sugerido', () => {
    expect(fieldsToRefresh(true, new Set(['weight', 'reps'])).size).toBe(4);
  });
});

describe('isLongText', () => {
  it('texto curto não ganha "Ver mais"', () => {
    expect(isLongText('Curto.')).toBe(false);
    expect(isLongText('x'.repeat(200))).toBe(true);
    expect(isLongText(null)).toBe(false);
  });
});

describe('createSubmitLatch (dupla submissão)', () => {
  it('segundo toque antes de terminar é barrado; reabre depois', () => {
    const latch = createSubmitLatch();
    expect(latch.tryEnter()).toBe(true);
    expect(latch.tryEnter()).toBe(false);
    expect(latch.isBusy()).toBe(true);
    latch.release();
    expect(latch.isBusy()).toBe(false);
    expect(latch.tryEnter()).toBe(true);
  });
});

describe('expectedRpeLine (#146)', () => {
  it('mostra o RPE esperado da série; sem valor, nada', () => {
    expect(expectedRpeLine(8)).toBe('RPE esperado: 8');
    expect(expectedRpeLine(8.5)).toBe('RPE esperado: 8.5');
    expect(expectedRpeLine(null)).toBeNull();
    expect(expectedRpeLine(undefined)).toBeNull();
  });
});

describe('initialRpe (#146)', () => {
  it('o esperado da série vence prescrito, sugestão da IA e série anterior', () => {
    expect(initialRpe({ expected: 8, prescribed: 9, fallback: 7 })).toBe(8);
  });
  it('sem esperado: alvo prescrito (modo avançado) e depois a cadeia', () => {
    expect(initialRpe({ expected: null, prescribed: 9, fallback: 7 })).toBe(9);
    expect(initialRpe({ expected: null, fallback: 7.5 })).toBe(7.5);
  });
  it('o fallback (IA, última série ou alvo) preenche sem esperado', () => {
    expect(initialRpe({ fallback: 6 })).toBe(6);
  });
  it('nada disponível: null', () => {
    expect(initialRpe({})).toBeNull();
    expect(initialRpe({ expected: undefined, prescribed: null, fallback: null })).toBeNull();
  });
});
