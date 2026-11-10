import { describe, expect, it } from 'vitest';
import {
  applyRestSetPatch,
  canEditRestSet,
  createSequenceGuard,
  restSetDraftFrom,
  restSetPatchBody,
  restSetResponseApplies,
  restSetSummary,
} from './workout-mode-rest-set-edit';
import { MEASURE_VALUES } from './set-measures';

const ORIGINAL = { reps: 10, weight_kg: 32.5, rpe: 8 };

describe('restSetSummary', () => {
  it('mostra reps, carga na unidade do usuário e RPE', () => {
    expect(restSetSummary(ORIGINAL, 'kg').text).toBe('10 reps · 32.5 kg · RPE 8');
    expect(restSetSummary(ORIGINAL, 'lb').loadText).toBe('71.7 lb');
  });

  it('partes ausentes somem', () => {
    expect(restSetSummary({ reps: 1, weight_kg: null, rpe: null }, 'kg').text).toBe('1 rep');
  });
});

describe('canEditRestSet', () => {
  it('série de trabalho simples de repetições é editável', () => {
    expect(canEditRestSet({ reps: 10 }, 7)).toBe(true);
    expect(canEditRestSet({ reps: 10, set_type: 'working', measure: MEASURE_VALUES.reps }, 7)).toBe(true);
  });

  it.each([
    ['por lado', { reps: null, reps_left: 8, reps_right: 9 }],
    ['por tempo', { reps: null, measure: MEASURE_VALUES.duration }],
    ['reps extras', { reps: 10, extra_reps: 3 }],
    ['técnica', { reps: 10, technique_code: 'drop_set' }],
    ['âncora', { reps: 10, anchor_set_number: 2 }],
    ['aquecimento', { reps: 10, set_type: 'warmup' }],
  ])('sem botão: %s', (_name, candidate) => {
    expect(canEditRestSet(candidate, 7)).toBe(false);
  });

  it('exercício sem id: sem botão', () => {
    expect(canEditRestSet({ reps: 10 }, null)).toBe(false);
  });
});

describe('restSetPatchBody', () => {
  it('sem alteração: corpo vazio (fecha sem pedido)', () => {
    expect(restSetPatchBody(ORIGINAL, restSetDraftFrom(ORIGINAL, 'kg'), 'kg')).toEqual({});
    expect(restSetPatchBody(ORIGINAL, restSetDraftFrom(ORIGINAL, 'lb'), 'lb')).toEqual({});
  });

  it('envia só o campo alterado', () => {
    const draft = { ...restSetDraftFrom(ORIGINAL, 'kg'), reps: 12 };
    expect(restSetPatchBody(ORIGINAL, draft, 'kg')).toEqual({ reps: 12 });
    expect(restSetPatchBody(ORIGINAL, { ...draft, reps: 10, rpe: 9 }, 'kg')).toEqual({ rpe: 9 });
  });

  it('carga em lb vira kg no corpo', () => {
    const body = restSetPatchBody(ORIGINAL, { reps: 10, weight: 80, rpe: 8 }, 'lb');
    expect(body).toEqual({ weight_kg: 36.29 });
  });

  it('campo vazio não limpa o valor gravado', () => {
    expect(restSetPatchBody(ORIGINAL, { reps: null, weight: null, rpe: null }, 'kg')).toEqual({});
  });

  it('série sem carga gravada aceita carga nova', () => {
    expect(restSetPatchBody({ reps: 8, weight_kg: null, rpe: null }, { reps: 8, weight: 20, rpe: null }, 'kg')).toEqual({
      weight_kg: 20,
    });
  });
});

describe('applyRestSetPatch', () => {
  it('aplica o corpo sobre os valores gravados', () => {
    expect(applyRestSetPatch(ORIGINAL, { reps: 12, weight_kg: 35 })).toEqual({ reps: 12, weight_kg: 35, rpe: 8 });
    expect(applyRestSetPatch(ORIGINAL, {})).toEqual(ORIGINAL);
  });
});

describe('série comum de sessão em modo avançado (decisão do Tech Lead)', () => {
  it('a elegibilidade não depende do modo: só do tipo/técnica/âncora da série', () => {
    // `canEditRestSet` não recebe o modo da sessão: série de trabalho comum
    // gravada pelo salvamento de série é elegível também em sessão avançada;
    // etapa de técnica e linha ancorada seguem de fora.
    expect(canEditRestSet({ reps: 8, set_type: 'working' }, 3)).toBe(true);
    expect(canEditRestSet({ reps: 8, set_type: 'working', technique_code: 'drop_set' }, 3)).toBe(false);
    expect(canEditRestSet({ reps: 8, set_type: 'working', anchor_set_number: 2 }, 3)).toBe(false);
  });
});

describe('restSetResponseApplies', () => {
  it('aplica só quando o descanso vigente é o mesmo alvo', () => {
    expect(restSetResponseApplies({ token: 4 }, { token: 4 })).toBe(true);
  });

  it('descarta quando o descanso acabou ou outro começou', () => {
    expect(restSetResponseApplies(null, { token: 4 })).toBe(false);
    expect(restSetResponseApplies(undefined, { token: 4 })).toBe(false);
    expect(restSetResponseApplies({ token: 5 }, { token: 4 })).toBe(false);
  });
});

describe('createSequenceGuard', () => {
  it('só o último pedido vale', () => {
    const guard = createSequenceGuard();
    const first = guard.begin();
    const second = guard.begin();
    expect(guard.isCurrent(first)).toBe(false);
    expect(guard.isCurrent(second)).toBe(true);
  });

  it('invalidate descarta o pedido em voo', () => {
    const guard = createSequenceGuard();
    const seq = guard.begin();
    guard.invalidate();
    expect(guard.isCurrent(seq)).toBe(false);
    expect(guard.isCurrent(guard.begin())).toBe(true);
  });
});
