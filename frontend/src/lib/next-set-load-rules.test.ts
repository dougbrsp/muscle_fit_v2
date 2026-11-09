import { describe, expect, it } from 'vitest';
import { ApiError } from './api';
import motorConstants from './motor-constants.generated.json';
import {
  ACTION_OPTIONS,
  DRAFT_RULES_QUERY_KEY,
  EMPTY_SIMULATE_FORM,
  FATIGUE_BAND_OPTIONS,
  MOTOR_DEFAULT_RULESET,
  NEXT_SET_LOAD_RULES_QUERY_KEY,
  PUBLISHED_RULES_QUERY_KEY,
  RULE_LIMITS,
  UNKNOWN_FATIGUE_BAND,
  VARIABLE_OPTIONS,
  VERSIONS_RULES_QUERY_KEY,
  addGroupChild,
  addLeafChild,
  addRule,
  codeMessage,
  countLeaves,
  draftFromRuleSet,
  draftToPayload,
  duplicateRule,
  groupDepth,
  isPublishConflict,
  newGroup,
  newRuleDraft,
  opOptionsFor,
  removeRule,
  reorderRules,
  ruleSetErrorMessages,
  sameRuleSet,
  scopeOptionsFor,
  setChild,
  shapeIssues,
  simulateFacts,
  simulateMissing,
  updateRule,
  valueSourceOptionsFor,
  type GroupDraft,
  type RuleSetDraft,
} from './next-set-load-rules';
import type { RuleSetInput } from './next-set-load-rules-types';

const VOCABULARY = motorConstants.next_set_load.rule_vocabulary;
const LIMITS = motorConstants.next_set_load.rule_limits;

/** Conjunto v2 com árvore aninhada: (série >= 2 E (reps > teto OU RPE anterior < máximo)), +percentual. */
const EXAMPLE: RuleSetInput = {
  reps_floor: MOTOR_DEFAULT_RULESET.reps_floor,
  reps_ceiling: MOTOR_DEFAULT_RULESET.reps_ceiling,
  rules: [
    {
      position: 1,
      when: {
        logic: 'and',
        children: [
          { variable: 'set_index', scope: 'current', op: '>=', value: 2 },
          {
            logic: 'or',
            children: [
              { variable: 'reps', scope: 'current', op: '>', value: 'ceiling' },
              { variable: 'rpe', scope: 'previous', op: '<', value: 'max', ramp_cap: false },
            ],
          },
        ],
      },
      action: 'INCREASE_LOAD',
      magnitude: { kind: 'percent', value: LIMITS.magnitude_percent_max },
      text: 'texto do servidor',
    },
    {
      position: 2,
      when: {
        logic: 'and',
        children: [
          { variable: 'fatigue_band', scope: 'current', op: '=', value: 'heavily_fatigued' },
          { variable: 'reps', scope: 'current', op: 'between', value: LIMITS.reps_floor_min, value_to: 'floor' },
        ],
      },
      action: 'HOLD',
    },
  ],
};

describe('opções dos Selects vêm do vocabulário do dono', () => {
  it('variáveis, ação e escopos', () => {
    expect(VARIABLE_OPTIONS.map((o) => o.value)).toEqual(VOCABULARY.variables.map((v) => v.value));
    expect(ACTION_OPTIONS.map((o) => o.value)).toEqual(VOCABULARY.actions.map((a) => a.value));
  });

  it('escopo "série anterior" só onde a variável permite; operadores e valores por variável', () => {
    for (const variable of VOCABULARY.variables) {
      expect(scopeOptionsFor(variable.value).map((o) => o.value)).toEqual(variable.scopes);
      expect(opOptionsFor(variable.value).map((o) => o.value)).toEqual(variable.ops);
    }
    const fatigue = VOCABULARY.variables.find((v) => v.kind === 'enum');
    expect(valueSourceOptionsFor(fatigue?.value ?? '').map((o) => o.value)).toEqual(
      (fatigue as { enum_values: { value: string }[] }).enum_values.map((v) => v.value),
    );
    expect(valueSourceOptionsFor('rpe').map((o) => o.value)).toEqual(['number', 'max', 'target']);
  });

  it('faixa de fadiga do simulador: as do vocabulário menos "qualquer", mais "desconhecida"', () => {
    const values = FATIGUE_BAND_OPTIONS.map((o) => o.value);
    expect(values).not.toContain('any');
    expect(values.at(-1)).toBe(UNKNOWN_FATIGUE_BAND);
  });

  it('limites e padrão são os do JSON gerado', () => {
    expect(RULE_LIMITS).toEqual(LIMITS);
    expect(MOTOR_DEFAULT_RULESET).toEqual(motorConstants.next_set_load.default_ruleset);
  });
});

describe('query keys', () => {
  it('compartilham o prefixo (invalidação em bloco)', () => {
    for (const key of [PUBLISHED_RULES_QUERY_KEY, DRAFT_RULES_QUERY_KEY, VERSIONS_RULES_QUERY_KEY]) {
      expect(key.slice(0, NEXT_SET_LOAD_RULES_QUERY_KEY.length)).toEqual([...NEXT_SET_LOAD_RULES_QUERY_KEY]);
    }
  });
});

describe('rascunho <-> payload (v2)', () => {
  it('ida e volta da árvore: posições 1..n, sem `text`, HOLD sem magnitude, tokens/between preservados', () => {
    const payload = draftToPayload(draftFromRuleSet(EXAMPLE));
    expect(payload?.rules.map((rule) => rule.position)).toEqual([1, 2]);
    expect(payload?.rules[0].when).toEqual({
      logic: 'and',
      children: [
        { variable: 'set_index', scope: 'current', op: '>=', value: 2 },
        {
          logic: 'or',
          children: [
            { variable: 'reps', scope: 'current', op: '>', value: 'ceiling' },
            { variable: 'rpe', scope: 'previous', op: '<', value: 'max' },
          ],
        },
      ],
    });
    expect(payload?.rules[0].magnitude).toEqual({ kind: 'percent', value: LIMITS.magnitude_percent_max });
    expect('text' in (payload?.rules[0] ?? {})).toBe(false);
    expect(payload?.rules[1].magnitude).toBeUndefined();
    expect(payload?.rules[1].when?.children[1]).toEqual({
      variable: 'reps',
      scope: 'current',
      op: 'between',
      value: LIMITS.reps_floor_min,
      value_to: 'floor',
    });
  });

  it('padrão do sistema (árvore): perde `reason_code` e volta idêntico ao ser reenviado', () => {
    const payload = draftToPayload(draftFromRuleSet(MOTOR_DEFAULT_RULESET));
    expect(payload?.rules).toHaveLength(MOTOR_DEFAULT_RULESET.rules.length);
    expect(payload?.rules.every((rule) => !('reason_code' in rule))).toBe(true);
    const again = draftToPayload(draftFromRuleSet(payload as RuleSetInput));
    expect(again).toEqual(payload);
    // `ramp_cap` do padrão sobrevive à ida e volta.
    expect(JSON.stringify(payload)).toContain('"ramp_cap":true');
  });

  it('ordena pela `position` recebida', () => {
    const reversed: RuleSetInput = {
      ...EXAMPLE,
      rules: [
        { ...EXAMPLE.rules[1], position: 2 },
        { ...EXAMPLE.rules[0], position: 1 },
      ],
    };
    const payload = draftToPayload(draftFromRuleSet(reversed));
    expect(payload?.rules.map((rule) => rule.action)).toEqual(['INCREASE_LOAD', 'HOLD']);
  });

  it('campo vazio -> null (não envia); ramp_cap sem valor é aceito', () => {
    const draft = draftFromRuleSet(EXAMPLE);
    const leaf = draft.rules[0].when.children[0];
    if (leaf.kind !== 'leaf') throw new Error('folha esperada');
    leaf.value = { source: 'number', number: '' };
    expect(draftToPayload(draft)).toBeNull();
    leaf.rampCap = true;
    expect(draftToPayload(draft)?.rules[0].when?.children[0]).toEqual({
      variable: 'set_index',
      scope: 'current',
      op: '>=',
      ramp_cap: true,
    });
    expect(draftToPayload({ ...draftFromRuleSet(EXAMPLE), reps_floor: '' })).toBeNull();
  });

  it('ENGINE_TARGET aceita magnitude ausente (opcional); ação obrigatória sem magnitude não envia', () => {
    const draft = draftFromRuleSet({
      ...EXAMPLE,
      rules: [{ position: 1, when: EXAMPLE.rules[0].when, action: 'ENGINE_TARGET' }],
    });
    expect(draft.rules[0].magnitude.value).toBe('');
    expect(draftToPayload(draft)?.rules[0].magnitude).toBeUndefined();
    expect(shapeIssues(draft)).toEqual([]);
    const required = { ...draft, rules: [{ ...draft.rules[0], action: 'INCREASE_LOAD' }] };
    expect(draftToPayload(required)).toBeNull();
  });

  it('sameRuleSet ignora ids e texto; nota conta', () => {
    const a = draftFromRuleSet(EXAMPLE, 'nota');
    const b = draftFromRuleSet(EXAMPLE, ' nota ');
    expect(sameRuleSet(a, b)).toBe(true);
    expect(sameRuleSet(a, { ...b, notes: 'outra' })).toBe(false);
  });
});

describe('contrato v2 e magnitude com sinal', () => {
  it('regra sem when é erro explícito, sem conversão local', () => {
    expect(() =>
      draftFromRuleSet({ reps_floor: 6, reps_ceiling: 12, rules: [{ position: 1, action: 'HOLD' }] }),
    ).toThrow(/when/);
  });

  it('ENGINE_TARGET aceita magnitude negativa (ida e volta); INCREASE_LOAD negativa é inválida', () => {
    const rule = (action: string) => ({
      position: 1,
      when: EXAMPLE.rules[0].when,
      action,
      magnitude: { kind: 'steps', value: -1 },
    });
    const engine = draftFromRuleSet({ reps_floor: 6, reps_ceiling: 12, rules: [rule('ENGINE_TARGET')] });
    expect(shapeIssues(engine)).toEqual([]);
    expect(draftToPayload(engine)?.rules[0].magnitude).toEqual({ kind: 'steps', value: -1 });
    const inc = draftFromRuleSet({ reps_floor: 6, reps_ceiling: 12, rules: [rule('INCREASE_LOAD')] });
    expect(shapeIssues(inc).length).toBeGreaterThan(0);
  });
});

describe('edição da lista e da árvore', () => {
  it('reordenar reatribui a posição pela ordem', () => {
    const draft = draftFromRuleSet(EXAMPLE);
    const moved = reorderRules(draft.rules, 0, 1);
    expect(draftToPayload({ ...draft, rules: moved })?.rules.map((r) => [r.position, r.action])).toEqual([
      [1, 'HOLD'],
      [2, 'INCREASE_LOAD'],
    ]);
    expect(reorderRules(draft.rules, 0, 9)).toEqual(draft.rules);
  });

  it('adicionar, duplicar (ids novos em toda a árvore) e remover', () => {
    const draft = draftFromRuleSet(EXAMPLE);
    const added = addRule(draft.rules);
    expect(added).toHaveLength(3);
    const duplicated = duplicateRule(draft.rules, draft.rules[0].localId);
    expect(duplicated).toHaveLength(3);
    expect(duplicated[1].localId).not.toBe(duplicated[0].localId);
    expect(duplicated[1].when.id).not.toBe(duplicated[0].when.id);
    expect(JSON.stringify(draftToPayload({ ...draft, rules: [duplicated[0]] })?.rules[0].when)).toBe(
      JSON.stringify(draftToPayload({ ...draft, rules: [duplicated[1]] })?.rules[0].when),
    );
    expect(removeRule(duplicated, duplicated[1].localId)).toHaveLength(2);
  });

  it('regra nova já é enviável', () => {
    const draft: RuleSetDraft = { reps_floor: 6, reps_ceiling: 12, rules: [newRuleDraft()], notes: '' };
    expect(shapeIssues(draft)).toEqual([]);
    expect(draftToPayload(draft)).not.toBeNull();
  });

  it('subgrupo alterna a lógica do pai; contagem de folhas e profundidade', () => {
    const root = draftFromRuleSet(EXAMPLE).rules[0].when;
    expect(countLeaves(root)).toBe(3);
    expect(groupDepth(root)).toBe(2);
    const next = addGroupChild(root);
    expect(next.children.at(-1)).toMatchObject({ kind: 'group', logic: 'or' });
    expect(addLeafChild(root).children).toHaveLength(root.children.length + 1);
    expect(setChild(root, 0, null).children).toHaveLength(root.children.length - 1);
  });

  it('updateRule apaga o texto do servidor da regra editada', () => {
    const draft = draftFromRuleSet(EXAMPLE);
    expect(draft.rules[0].text).toBe('texto do servidor');
    const next = updateRule(draft.rules, draft.rules[0].localId, { action: 'DECREASE_LOAD' });
    expect(next[0].text).toBeNull();
    expect(next[1].text).toBe(draft.rules[1].text);
  });
});

describe('validação de forma por rule_limits', () => {
  it('exemplo válido', () => {
    expect(shapeIssues(draftFromRuleSet(EXAMPLE))).toEqual([]);
  });

  it('profundidade acima de max_depth', () => {
    const draft = draftFromRuleSet(EXAMPLE);
    let deep: GroupDraft = newGroup('or');
    for (let i = 0; i < LIMITS.max_depth; i += 1) deep = newGroup('or', [deep]);
    deep = { ...draft.rules[0].when, children: [deep] };
    expect(groupDepth(deep)).toBeGreaterThan(LIMITS.max_depth);
    const issues = shapeIssues({ ...draft, rules: [{ ...draft.rules[0], when: deep }] });
    expect(issues.some((message) => message.includes('níveis de parênteses'))).toBe(true);
  });

  it('folhas acima de max_leaves_per_rule e grupo vazio', () => {
    const draft = draftFromRuleSet(EXAMPLE);
    let when = draft.rules[0].when;
    for (let i = 0; i < LIMITS.max_leaves_per_rule; i += 1) when = addLeafChild(when);
    const tooMany = shapeIssues({ ...draft, rules: [{ ...draft.rules[0], when }] });
    expect(tooMany.some((message) => message.includes(`no máximo ${LIMITS.max_leaves_per_rule} condições`))).toBe(true);
    const empty = shapeIssues({ ...draft, rules: [{ ...draft.rules[0], when: { ...when, children: [] } }] });
    expect(empty.some((message) => message.includes('grupo vazio'))).toBe(true);
  });

  it('números fora das faixas do JSON; passos inteiros; `between` exige o segundo valor; teto de regras', () => {
    const draft = draftFromRuleSet(EXAMPLE);
    const leaf = draft.rules[0].when.children[0];
    if (leaf.kind !== 'leaf') throw new Error('folha esperada');
    leaf.value = { source: 'number', number: LIMITS.set_index_max + 1 };
    expect(shapeIssues(draft).join(' ')).toContain(`entre 1 e ${LIMITS.set_index_max}`);
    leaf.value = { source: 'number', number: 2 };
    draft.rules[0].magnitude = { kind: 'steps', value: 1.5 };
    expect(shapeIssues(draft).join(' ')).toContain('inteiros');
    draft.rules[0].magnitude = { kind: 'percent', value: LIMITS.magnitude_percent_max + 1 };
    expect(shapeIssues(draft).join(' ')).toContain('magnitude entre');
    const second = draft.rules[1].when.children[1];
    if (second.kind !== 'leaf') throw new Error('folha esperada');
    second.valueTo = { source: 'number', number: '' };
    expect(shapeIssues(draft).join(' ')).toContain('informe o valor');
    const many = { ...draft, rules: Array.from({ length: LIMITS.max_rules + 1 }, () => draft.rules[1]) };
    expect(shapeIssues(many).join(' ')).toContain(`No máximo ${LIMITS.max_rules} regras`);
  });

  it('HOLD não valida magnitude', () => {
    const draft = draftFromRuleSet(EXAMPLE);
    draft.rules[1].magnitude = { kind: 'steps', value: '' };
    expect(shapeIssues(draft)).toEqual([]);
  });
});

describe('códigos e erros do servidor', () => {
  it('código com posição vira "Regra N: …"; desconhecido sai como veio', () => {
    expect(codeMessage('RULE_MAGNITUDE_MISSING:3')).toBe('Regra 3: informe a magnitude.');
    expect(codeMessage('RULESET_EMPTY')).toBe('O conjunto precisa de ao menos uma regra.');
    expect(codeMessage('CODIGO_NOVO:2')).toBe('Regra 2: CODIGO_NOVO');
  });

  it('códigos novos da árvore têm mensagem própria', () => {
    const codes = [
      'RULE_GROUP_EMPTY',
      'RULE_DEPTH_EXCEEDED',
      'RULE_TOO_MANY_LEAVES',
      'RULE_VARIABLE_UNKNOWN',
      'RULE_SCOPE_INVALID',
      'RULE_OP_INVALID_FOR_VARIABLE',
      'RULE_RAMP_CAP_INVALID',
      'RULE_ENGINE_TARGET_MAGNITUDE_INVALID',
      'RULE_SET_INDEX_OUT_OF_RANGE',
      'RULE_ENUM_VALUE_UNKNOWN',
      'RULE_LOGIC_UNKNOWN',
    ];
    for (const code of codes) {
      expect(codeMessage(`${code}:2`)).not.toBe(`Regra 2: ${code}`);
    }
    expect(codeMessage('RULE_DEPTH_EXCEEDED')).toContain(String(LIMITS.max_depth));
    expect(codeMessage('RULE_TOO_MANY_LEAVES')).toContain(String(LIMITS.max_leaves_per_rule));
  });

  it('422 com `codes` -> uma mensagem por código', () => {
    const error = new ApiError('x', 422, {
      error: 'RULESET_INVALID',
      message: 'inválido',
      codes: ['RULE_GROUP_EMPTY:1', 'RULESET_EMPTY'],
    });
    expect(ruleSetErrorMessages(error, 'falhou')).toEqual([
      'Regra 1: há um grupo de condições vazio.',
      'O conjunto precisa de ao menos uma regra.',
    ]);
  });

  it('409 é conflito de publicação; 422 do Pydantic tem mensagem genérica', () => {
    expect(isPublishConflict(new ApiError('x', 409, { error: 'CONFLICT', message: 'm' }))).toBe(true);
    expect(isPublishConflict(new ApiError('x', 422, []))).toBe(false);
    expect(ruleSetErrorMessages(new ApiError('x', 422, [{ loc: ['body'] }]), 'falhou')[0]).toContain('Formato');
    expect(ruleSetErrorMessages(new Error('rede'), 'falhou')).toEqual(['falhou']);
  });

  it('403 da API mostra o detail; 403 sem corpo da API é da borda (WAF)', () => {
    expect(ruleSetErrorMessages(new ApiError('Apenas admin', 403, 'Apenas admin'), 'f')).toEqual(['Apenas admin']);
    expect(ruleSetErrorMessages(new ApiError('Erro (HTTP 403)', 403, undefined), 'f')[0]).toContain('WAF');
  });
});

describe('simulador', () => {
  it('formulário -> fatos com série anterior, alvo do motor e RPE alvo; faixa desconhecida = null', () => {
    const facts = simulateFacts({
      ...EMPTY_SIMULATE_FORM,
      working_set_index: 2,
      fatigue_band: UNKNOWN_FATIGUE_BAND,
      reps: 8,
      rpe: 9.5,
      rpe_target: 8,
      weight_kg: 80,
      step_kg: 2.5,
      engine_target_kg: 82.5,
      previous_reps: 10,
      previous_rpe: 8,
      joint_pain: true,
      muscle_pain: 3,
    });
    expect(facts).toEqual({
      working_set_index: 2,
      fatigue_band: null,
      reps: 8,
      rpe: 9.5,
      rpe_ceiling: null,
      planned_rpe: null,
      rpe_target: 8,
      weight_kg: 80,
      step_kg: 2.5,
      engine_target_kg: 82.5,
      joint_pain: true,
      muscle_pain: 3,
      previous: { reps: 10, rpe: 8 },
    });
  });

  it('sem série anterior: previous = null; vazios = null', () => {
    const facts = simulateFacts(EMPTY_SIMULATE_FORM);
    expect(facts.previous).toBeNull();
    expect(facts.engine_target_kg).toBeNull();
    expect(facts.reps).toBeNull();
    expect(simulateFacts({ ...EMPTY_SIMULATE_FORM, previous_rpe: 8 }).previous).toEqual({ reps: null, rpe: 8 });
  });

  it('pede reps, carga e passo', () => {
    expect(simulateMissing(EMPTY_SIMULATE_FORM)).toEqual(['reps', 'carga', 'passo']);
    expect(simulateMissing({ ...EMPTY_SIMULATE_FORM, reps: 5, weight_kg: 10, step_kg: 2 })).toEqual([]);
  });
});
