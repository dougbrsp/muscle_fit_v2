import { describe, expect, it } from 'vitest';
import {
  EMPTY_SIMULATE_FORM,
  RULE_LIMITS,
  codeMessage,
  draftFromRuleSet,
  draftToPayload,
  groupDepth,
  newGroup,
  sameRuleSet,
  shapeIssues,
  simulateFacts,
  type GroupDraft,
  type LeafDraft,
  type NodeDraft,
  type RuleSetDraft,
} from './next-set-load-rules';
import type { ConditionLeafPayload, RulePayload, RuleSetInput } from './next-set-load-rules-types';

// Regra 1 do usuário: SE série = 1 ENTÃO carga = recomendação do motor.
const REGRA_1: RulePayload = {
  position: 1,
  when: { logic: 'and', children: [{ variable: 'set_index', scope: 'current', op: '=', value: 1 }] },
  action: 'ENGINE_TARGET',
};

// Regra 2 do usuário (com sub-grupo da série anterior) + 1 passo.
const REGRA_2: RulePayload = {
  position: 2,
  when: {
    logic: 'and',
    children: [
      { variable: 'set_index', scope: 'current', op: '=', value: 2 },
      { variable: 'fatigue_band', scope: 'current', op: '=', value: 'rested' },
      { variable: 'reps', scope: 'current', op: '>=', value: 'ceiling' },
      { variable: 'rpe', scope: 'current', op: '<', value: 'target' },
      {
        logic: 'and',
        children: [
          { variable: 'rpe', scope: 'previous', op: '=', value: 'max' },
          { variable: 'reps', scope: 'previous', op: '>=', value: 'ceiling' },
        ],
      },
    ],
  },
  action: 'ENGINE_TARGET',
  magnitude: { kind: 'steps', value: 1 },
};

const CONJUNTO: RuleSetInput = { reps_floor: 6, reps_ceiling: 12, rules: [REGRA_1, REGRA_2] };

function folhas(group: GroupDraft): LeafDraft[] {
  return group.children.flatMap((child: NodeDraft) => (child.kind === 'leaf' ? [child] : folhas(child)));
}

function aninhado(niveis: number): GroupDraft {
  let group = newGroup('and');
  for (let i = 1; i < niveis; i += 1) group = newGroup('or', [group]);
  return group;
}

function rascunhoComRaiz(when: GroupDraft): RuleSetDraft {
  const base = draftFromRuleSet({ reps_floor: 6, reps_ceiling: 12, rules: [REGRA_1] });
  return { ...base, rules: [{ ...base.rules[0], when }] };
}

describe('ida e volta dos exemplos do usuário pelo rascunho', () => {
  it('rascunho -> payload devolve exatamente o que entrou (sem text/reason_code/campos v1)', () => {
    const payload = draftToPayload(draftFromRuleSet(CONJUNTO));
    expect(payload).toEqual(CONJUNTO);
    for (const rule of payload?.rules ?? []) {
      expect(rule).not.toHaveProperty('text');
      expect(rule).not.toHaveProperty('reason_code');
      expect(rule).not.toHaveProperty('set_position');
      expect(rule).not.toHaveProperty('respect_planned_ramp');
    }
  });

  it('draftFromRuleSet -> draftToPayload -> draftFromRuleSet não perde nada', () => {
    const primeiro = draftFromRuleSet(CONJUNTO);
    const payload = draftToPayload(primeiro);
    expect(payload).not.toBeNull();
    const segundo = draftFromRuleSet(payload as RuleSetInput);
    expect(sameRuleSet(primeiro, segundo)).toBe(true);
    expect(draftToPayload(segundo)).toEqual(CONJUNTO);
  });

  it('a regra 2 mantém as 6 folhas e o escopo previous', () => {
    const regra = draftFromRuleSet(CONJUNTO).rules[1];
    const todas = folhas(regra.when);
    expect(todas).toHaveLength(6);
    expect(todas.filter((leaf) => leaf.scope === 'previous').map((leaf) => leaf.variable)).toEqual(['rpe', 'reps']);
    expect(groupDepth(regra.when)).toBe(2);
  });

  it('as posições saem 1..n pela ordem da lista, mesmo com position desordenado na entrada', () => {
    const invertido: RuleSetInput = {
      ...CONJUNTO,
      rules: [
        { ...REGRA_2, position: 9 },
        { ...REGRA_1, position: 4 },
      ],
    };
    const payload = draftToPayload(draftFromRuleSet(invertido));
    expect(payload?.rules.map((rule) => rule.position)).toEqual([1, 2]);
    expect(payload?.rules.map((rule) => rule.action)).toEqual(['ENGINE_TARGET', 'ENGINE_TARGET']);
    expect(payload?.rules[0].magnitude).toBeUndefined();
    expect(payload?.rules[1].magnitude).toEqual({ kind: 'steps', value: 1 });
  });

  it('o payload não carrega avaliação local (nenhuma chave de resultado)', () => {
    const payload = draftToPayload(draftFromRuleSet(CONJUNTO)) as unknown as Record<string, unknown>;
    expect(Object.keys(payload).sort()).toEqual(['reps_ceiling', 'reps_floor', 'rules']);
  });

  it('campo vazio impede o payload (null) em vez de inventar valor', () => {
    const rascunho = draftFromRuleSet(CONJUNTO);
    expect(draftToPayload({ ...rascunho, reps_floor: '' })).toBeNull();
  });
});

describe('contrato v2 do servidor', () => {
  it('regra sem when lança erro citando when', () => {
    const semWhen = { reps_floor: 6, reps_ceiling: 12, rules: [{ position: 1, action: 'HOLD' }] } as RuleSetInput;
    expect(() => draftFromRuleSet(semWhen)).toThrow(/when/);
  });

  it('ida e volta de folha rpe com ramp_cap dentro de grupo and', () => {
    const folhaRampa: ConditionLeafPayload = { variable: 'rpe', scope: 'current', op: '<', value: 'max', ramp_cap: true };
    const x: RuleSetInput = {
      reps_floor: 6,
      reps_ceiling: 12,
      rules: [{ position: 1, when: { logic: 'and', children: [folhaRampa] }, action: 'HOLD' }],
    };
    const rascunho = draftFromRuleSet(x);
    expect(folhas(rascunho.rules[0].when)[0].rampCap).toBe(true);
    expect(draftToPayload(rascunho)?.rules).toEqual(x.rules);
  });
});

describe('limites de forma', () => {
  it('aceita profundidade máxima e rejeita máxima + 1', () => {
    const max = RULE_LIMITS.max_depth;
    const valido = rascunhoComRaiz(aninhado(max));
    expect(groupDepth(valido.rules[0].when)).toBe(max);
    expect(shapeIssues(valido).filter((issue) => issue.includes('níveis'))).toEqual([]);
    const invalido = rascunhoComRaiz(aninhado(max + 1));
    expect(groupDepth(invalido.rules[0].when)).toBe(max + 1);
    expect(shapeIssues(invalido).some((issue) => issue.includes('níveis'))).toBe(true);
  });

  it('limita folhas por regra', () => {
    const limite = RULE_LIMITS.max_leaves_per_rule;
    const nGrupo = (n: number) => newGroup('and', Array.from({ length: n }, () => newGroup('and').children[0]));
    const ok = shapeIssues(rascunhoComRaiz(nGrupo(limite))).filter((issue) => issue.includes('condições por regra'));
    expect(ok).toEqual([]);
    const demais = shapeIssues(rascunhoComRaiz(nGrupo(limite + 1)));
    expect(demais.some((issue) => issue.includes('condições por regra'))).toBe(true);
  });
});

describe('codeMessage dos códigos novos', () => {
  const CODIGOS_NOVOS = [
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

  it.each(CODIGOS_NOVOS)('%s tem texto legível próprio', (codigo) => {
    const mensagem = codeMessage(codigo);
    expect(mensagem.trim().length).toBeGreaterThan(0);
    expect(mensagem).not.toBe(codigo);
  });

  it('prefixa a regra quando o código vem com posição', () => {
    expect(codeMessage('RULE_DEPTH_EXCEEDED:2')).toBe(`Regra 2: ${codeMessage('RULE_DEPTH_EXCEEDED')}`);
  });
});

describe('simulateFacts', () => {
  it('só repassa os fatos (sem avaliar): anterior ausente vira null, alvos viram número', () => {
    const vazio = simulateFacts(EMPTY_SIMULATE_FORM);
    expect(vazio.previous).toBeNull();
    expect(vazio.rpe_target).toBeNull();
    expect(vazio.engine_target_kg).toBeNull();
    const cheio = simulateFacts({
      ...EMPTY_SIMULATE_FORM,
      working_set_index: 2,
      reps: 12,
      rpe: 7,
      rpe_target: 8,
      engine_target_kg: 30,
      previous_reps: 12,
      previous_rpe: 10,
    });
    expect(cheio).toMatchObject({
      working_set_index: 2,
      rpe_target: 8,
      engine_target_kg: 30,
      previous: { reps: 12, rpe: 10 },
    });
  });
});
