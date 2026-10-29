import { describe, expect, it } from 'vitest';
import { ApiError } from './api';
import motorConstants from './motor-constants.generated.json';
import {
  ACTION_OPTIONS,
  CONDITION_OP_OPTIONS,
  DRAFT_RULES_QUERY_KEY,
  FATIGUE_BAND_OPTIONS,
  FATIGUE_OPTIONS,
  MAGNITUDE_KIND_OPTIONS,
  MOTOR_DEFAULT_RULESET,
  NEXT_SET_LOAD_RULES_QUERY_KEY,
  NO_CONDITION,
  NUMBER_SOURCE,
  PUBLISHED_RULES_QUERY_KEY,
  REPS_VALUE_SOURCE_OPTIONS,
  RPE_VALUE_SOURCE_OPTIONS,
  RULE_LIMITS,
  SET_POSITION_OPTIONS,
  UNKNOWN_FATIGUE_BAND,
  VERSIONS_RULES_QUERY_KEY,
  EDGE_BLOCKED_MESSAGE,
  EMPTY_SIMULATE_FORM,
  addRule,
  codeMessage,
  draftFromRuleSet,
  draftToPayload,
  duplicateRule,
  emptyCondition,
  isPublishConflict,
  magnitudeBounds,
  newRuleDraft,
  removeRule,
  reorderRules,
  ruleSetErrorMessages,
  sameRuleSet,
  shapeIssues,
  simulateFacts,
  simulateMissing,
  updateRule,
} from './next-set-load-rules';
import type { RuleSetInput } from './next-set-load-rules-types';

const VOCABULARY = motorConstants.next_set_load.rule_vocabulary;
const LIMITS = motorConstants.next_set_load.rule_limits;

/** O exemplo do pedido (JSON do plano): 1ª série, descansado, reps > teto, RPE < máximo, +percentual. */
const EXAMPLE: RuleSetInput = {
  reps_floor: MOTOR_DEFAULT_RULESET.reps_floor,
  reps_ceiling: MOTOR_DEFAULT_RULESET.reps_ceiling,
  rules: [
    {
      position: 1,
      set_position: 'first',
      fatigue: 'rested',
      reps: { op: '>', value: 'ceiling' },
      rpe: { op: '<', value: 'max' },
      respect_planned_ramp: true,
      action: 'INCREASE_LOAD',
      magnitude: { kind: 'percent', value: LIMITS.magnitude_percent_max },
      text: 'texto do servidor',
    },
    {
      position: 2,
      set_position: 'any',
      fatigue: 'heavily_fatigued',
      reps: { op: 'between', value: LIMITS.reps_floor_min, value_to: 'floor' },
      rpe: null,
      respect_planned_ramp: false,
      action: 'HOLD',
      magnitude: null,
    },
  ],
};

describe('opções dos Selects vêm do vocabulário do dono', () => {
  it('posição, fadiga, ação e tipo de magnitude', () => {
    expect(SET_POSITION_OPTIONS).toEqual(VOCABULARY.set_positions);
    expect(FATIGUE_OPTIONS).toEqual(VOCABULARY.fatigue_conditions);
    expect(ACTION_OPTIONS).toEqual(VOCABULARY.actions);
    expect(MAGNITUDE_KIND_OPTIONS).toEqual(VOCABULARY.magnitude_kinds);
  });

  it('operadores com "sem condição" na frente; tokens depois de "número"', () => {
    expect(CONDITION_OP_OPTIONS[0].value).toBe(NO_CONDITION);
    expect(CONDITION_OP_OPTIONS.slice(1)).toEqual(VOCABULARY.condition_ops);
    expect(REPS_VALUE_SOURCE_OPTIONS.map((option) => option.value)).toEqual([
      NUMBER_SOURCE,
      ...VOCABULARY.reps_tokens.map((token) => token.value),
    ]);
    expect(RPE_VALUE_SOURCE_OPTIONS.map((option) => option.value)).toEqual([
      NUMBER_SOURCE,
      ...VOCABULARY.rpe_tokens.map((token) => token.value),
    ]);
  });

  it('faixa de fadiga do simulador: as do vocabulário menos "qualquer", mais "desconhecida"', () => {
    expect(FATIGUE_BAND_OPTIONS.map((option) => option.value)).toEqual([
      ...VOCABULARY.fatigue_conditions.map((item) => item.value).filter((value) => value !== 'any'),
      UNKNOWN_FATIGUE_BAND,
    ]);
  });

  it('limites e padrão são os do JSON gerado', () => {
    expect(RULE_LIMITS).toEqual(LIMITS);
    expect(MOTOR_DEFAULT_RULESET).toEqual(motorConstants.next_set_load.default_ruleset);
    expect(magnitudeBounds('percent')).toEqual({
      min: LIMITS.magnitude_percent_min,
      max: LIMITS.magnitude_percent_max,
      integer: false,
    });
    expect(magnitudeBounds('steps')).toEqual({
      min: LIMITS.magnitude_steps_min,
      max: LIMITS.magnitude_steps_max,
      integer: true,
    });
  });
});

describe('query keys', () => {
  it('compartilham o prefixo (invalidação em bloco)', () => {
    for (const key of [PUBLISHED_RULES_QUERY_KEY, DRAFT_RULES_QUERY_KEY, VERSIONS_RULES_QUERY_KEY]) {
      expect(key.slice(0, NEXT_SET_LOAD_RULES_QUERY_KEY.length)).toEqual([...NEXT_SET_LOAD_RULES_QUERY_KEY]);
    }
  });
});

describe('rascunho <-> payload', () => {
  it('round-trip: posições 1..n, sem `text`, HOLD sem magnitude, tokens e `between` preservados', () => {
    const draft = draftFromRuleSet(EXAMPLE, 'nota');
    expect(draft.notes).toBe('nota');
    expect(draft.rules[0].text).toBe('texto do servidor');
    expect(new Set(draft.rules.map((rule) => rule.localId)).size).toBe(2);
    const payload = draftToPayload(draft);
    expect(payload).toEqual({
      reps_floor: EXAMPLE.reps_floor,
      reps_ceiling: EXAMPLE.reps_ceiling,
      rules: EXAMPLE.rules.map(({ text: _text, ...rule }) => rule),
    });
  });

  it('padrão do sistema: perde `reason_code` (regra cadastrada usa o motivo genérico)', () => {
    const payload = draftToPayload(draftFromRuleSet(MOTOR_DEFAULT_RULESET));
    expect(payload?.rules).toHaveLength(MOTOR_DEFAULT_RULESET.rules.length);
    for (const rule of payload?.rules ?? []) expect(rule).not.toHaveProperty('reason_code');
  });

  it('ordena pela `position` recebida', () => {
    const shuffled: RuleSetInput = { ...EXAMPLE, rules: [EXAMPLE.rules[1], EXAMPLE.rules[0]] };
    const payload = draftToPayload(draftFromRuleSet(shuffled));
    expect(payload?.rules.map((rule) => rule.set_position)).toEqual(['first', 'any']);
  });

  it('campo numérico vazio -> null (não envia)', () => {
    const draft = draftFromRuleSet(EXAMPLE);
    draft.rules[0].reps = emptyCondition('>');
    expect(draftToPayload(draft)).toBeNull();
    expect(draftToPayload({ ...draftFromRuleSet(EXAMPLE), reps_floor: '' })).toBeNull();
  });

  it('"sem condição" vira null', () => {
    const draft = draftFromRuleSet(EXAMPLE);
    draft.rules[0].rpe = { ...emptyCondition(NO_CONDITION) };
    expect(draftToPayload(draft)?.rules[0].rpe).toBeNull();
  });

  it('sameRuleSet ignora localId e texto; nota conta', () => {
    const a = draftFromRuleSet(EXAMPLE, 'x');
    const b = draftFromRuleSet(EXAMPLE, 'x');
    expect(sameRuleSet(a, b)).toBe(true);
    expect(sameRuleSet(a, { ...b, notes: 'y' })).toBe(false);
    expect(sameRuleSet(a, { ...b, rules: reorderRules(b.rules, 0, 1) })).toBe(false);
  });
});

describe('edição da lista', () => {
  it('reordenar reatribui a posição pela ordem', () => {
    const draft = draftFromRuleSet(EXAMPLE);
    const moved = reorderRules(draft.rules, 1, 0);
    expect(moved.map((rule) => rule.localId)).toEqual([draft.rules[1].localId, draft.rules[0].localId]);
    const payload = draftToPayload({ ...draft, rules: moved });
    expect(payload?.rules.map((rule) => [rule.position, rule.set_position])).toEqual([
      [1, 'any'],
      [2, 'first'],
    ]);
    expect(reorderRules(draft.rules, 0, 9)).toEqual(draft.rules);
  });

  it('adicionar, duplicar (logo abaixo, novo localId) e remover', () => {
    const draft = draftFromRuleSet(EXAMPLE);
    const added = addRule(draft.rules);
    expect(added).toHaveLength(3);
    const copy = duplicateRule(added, added[0].localId);
    expect(copy).toHaveLength(4);
    expect(copy[1].localId).not.toBe(copy[0].localId);
    expect(draftToPayload({ ...draft, rules: copy })?.rules[1]).toEqual({
      ...draftToPayload({ ...draft, rules: added })?.rules[0],
      position: 2,
    });
    expect(removeRule(copy, copy[1].localId).map((rule) => rule.localId)).toEqual(
      added.map((rule) => rule.localId),
    );
  });

  it('regra nova já é enviável e usa o mínimo de passos do JSON', () => {
    const rule = newRuleDraft();
    expect(rule.magnitude.value).toBe(magnitudeBounds(rule.magnitude.kind).min);
    expect(shapeIssues({ reps_floor: LIMITS.reps_floor_min, reps_ceiling: LIMITS.reps_ceiling_max, rules: [rule], notes: '' })).toEqual([]);
  });

  it('updateRule apaga o texto do servidor da regra editada', () => {
    const draft = draftFromRuleSet(EXAMPLE);
    const next = updateRule(draft.rules, draft.rules[0].localId, { fatigue: 'any' });
    expect(next[0].text).toBeNull();
    expect(next[0].fatigue).toBe('any');
  });
});

describe('validação de forma por rule_limits', () => {
  it('exemplo válido', () => {
    expect(shapeIssues(draftFromRuleSet(EXAMPLE))).toEqual([]);
  });

  it('números fora dos limites do JSON', () => {
    const draft = draftFromRuleSet(EXAMPLE);
    draft.reps_floor = LIMITS.reps_floor_min - 1;
    draft.reps_ceiling = LIMITS.reps_ceiling_max + 1;
    draft.rules[0].magnitude = { kind: 'percent', value: LIMITS.magnitude_percent_max + 1 };
    draft.rules[0].rpe = { ...emptyCondition('<'), value: { source: NUMBER_SOURCE, number: LIMITS.rule_rpe_max + 1 } };
    draft.rules[1].reps = { ...emptyCondition('>'), value: { source: NUMBER_SOURCE, number: LIMITS.reps_ceiling_max + 1 } };
    const issues = shapeIssues(draft);
    expect(issues).toHaveLength(5);
    expect(issues.join(' ')).toContain('Regra 1');
    expect(issues.join(' ')).toContain('Regra 2');
  });

  it('passos inteiros; `between` exige o segundo valor; teto de regras', () => {
    const draft = draftFromRuleSet(EXAMPLE);
    draft.rules[0].magnitude = { kind: 'steps', value: LIMITS.magnitude_steps_min + 0.5 };
    draft.rules[1].reps = { ...emptyCondition('between'), value: { source: NUMBER_SOURCE, number: LIMITS.reps_floor_min } };
    expect(shapeIssues(draft)).toHaveLength(2);
    let rules = draft.rules;
    while (rules.length <= LIMITS.max_rules) rules = addRule(rules);
    expect(shapeIssues({ ...draftFromRuleSet(EXAMPLE), rules }).some((issue) => issue.includes(String(LIMITS.max_rules)))).toBe(true);
  });

  it('HOLD não valida magnitude', () => {
    const draft = draftFromRuleSet(EXAMPLE);
    draft.rules[1].magnitude = { kind: 'steps', value: '' };
    expect(shapeIssues(draft)).toEqual([]);
  });
});

describe('códigos e erros do servidor', () => {
  it('código com posição vira "Regra N: …"; desconhecido sai como veio', () => {
    expect(codeMessage('RULE_MAGNITUDE_MISSING:3')).toMatch(/^Regra 3: /);
    expect(codeMessage('RULESET_EMPTY')).not.toContain('RULESET_EMPTY');
    expect(codeMessage('ALGO_NOVO')).toBe('ALGO_NOVO');
  });

  it('422 com `codes` -> uma mensagem por código', () => {
    const error = new ApiError('x', 422, {
      error: 'invalid_ruleset',
      message: 'Conjunto de regras fora das regras do motor.',
      codes: ['REPS_FLOOR_NOT_BELOW_CEILING', 'RULE_RAMP_WITHOUT_RPE:2'],
    });
    const messages = ruleSetErrorMessages(error, 'fallback');
    expect(messages).toHaveLength(2);
    expect(messages[1]).toMatch(/^Regra 2: /);
  });

  it('409 é conflito de publicação; 422 do Pydantic tem mensagem genérica', () => {
    expect(isPublishConflict(new ApiError('x', 409, { error: 'publish_conflict', message: 'm', current_version: 3 }))).toBe(true);
    expect(isPublishConflict(new ApiError('x', 422))).toBe(false);
    expect(ruleSetErrorMessages(new ApiError('x', 422, [{ loc: ['body'] }]), 'f')).toHaveLength(1);
    expect(ruleSetErrorMessages(new Error('boom'), 'fallback')).toEqual(['fallback']);
  });

  it('403 da API mostra o detail; 403 sem corpo da API é da borda (WAF)', () => {
    expect(ruleSetErrorMessages(new ApiError('Acesso restrito a administradores.', 403), 'f')).toEqual([
      'Acesso restrito a administradores.',
    ]);
    expect(
      ruleSetErrorMessages(new ApiError('Erro ao consultar /next-set-load-rules/draft (HTTP 403).', 403), 'f'),
    ).toEqual([EDGE_BLOCKED_MESSAGE]);
  });
});

describe('simulador', () => {
  it('formulário -> fatos; faixa desconhecida = null; vazios = null', () => {
    const facts = simulateFacts({
      ...EMPTY_SIMULATE_FORM,
      working_set_index: 1,
      fatigue_band: UNKNOWN_FATIGUE_BAND,
      reps: 13,
      weight_kg: 20,
      step_kg: 2.5,
    });
    expect(facts).toEqual({
      working_set_index: 1,
      fatigue_band: null,
      reps: 13,
      rpe: null,
      rpe_ceiling: null,
      planned_rpe: null,
      weight_kg: 20,
      step_kg: 2.5,
      joint_pain: false,
      muscle_pain: null,
    });
    expect(simulateFacts({ ...EMPTY_SIMULATE_FORM, fatigue_band: 'rested' }).fatigue_band).toBe('rested');
  });

  it('pede reps, carga e passo', () => {
    expect(simulateMissing(EMPTY_SIMULATE_FORM)).toEqual(['reps', 'carga', 'passo']);
    expect(simulateMissing({ ...EMPTY_SIMULATE_FORM, reps: 8, weight_kg: 20, step_kg: 1 })).toEqual([]);
  });
});
