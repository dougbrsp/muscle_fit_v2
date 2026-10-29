import { describe, expect, it } from 'vitest';
import { createSubmitLatch } from './submit-latch';
import { aiTipView, fieldsToRefresh, isLongText, setTargetLine } from './workout-mode-execution-view';
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
