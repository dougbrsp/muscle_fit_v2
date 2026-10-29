// RPE 10 Lote 6/P9 — "uma voz no Modo Treino": status do motor por série,
// decisão vigente e categoria da IA no vocabulário do motor. Só apresentação:
// rótulos, códigos e direções vêm do JSON gerado pelo backend.

import { describe, expect, it } from 'vitest';

import { PROGRESSION_DECISION_LABELS } from './exercise-progress';
import motorConstants from './motor-constants.generated.json';
import {
  engineDecisionText,
  loadAdjustmentText,
  nextSetLoadView,
  savedSetStatusViews,
  SET_EVALUATION_CATEGORY_LABELS,
  setEvaluationCategoryView,
} from './set-engine-status';

describe('paridade com o backend (motor-constants.generated.json)', () => {
  it('rótulos das ações do motor vêm de DECISION_LABELS (sem cópia manual)', () => {
    expect(PROGRESSION_DECISION_LABELS).toEqual(motorConstants.progression_decision_labels);
    expect(PROGRESSION_DECISION_LABELS.HOLD).toBe('Manter');
    expect(PROGRESSION_DECISION_LABELS.INCREASE_LOAD).toBe('Aumentar carga');
  });

  it('categorias da IA são ações do motor, com o mesmo rótulo', () => {
    const { categories, category_labels } = motorConstants.set_evaluation;
    expect(SET_EVALUATION_CATEGORY_LABELS).toEqual(category_labels);
    for (const code of categories) {
      expect(category_labels[code as keyof typeof category_labels]).toBe(PROGRESSION_DECISION_LABELS[code]);
    }
  });
});

describe('savedSetStatusViews', () => {
  it('esforço atingido em teal, com o rótulo do servidor', () => {
    expect(savedSetStatusViews({ intensity_status: 'REACHED', intensity_status_label: 'Esforço atingido' })).toEqual([
      { label: 'Esforço atingido', color: 'teal' },
    ]);
  });

  it('set-eval-v5: o status é de esforço; as reps contra a faixa vão ao lado, em selo neutro', () => {
    expect(motorConstants.intensity.intensity_status_labels.REACHED).toBe('Esforço atingido');
    expect(
      savedSetStatusViews({
        intensity_status: 'REACHED',
        intensity_status_label: 'Esforço atingido',
        reps_position: 'BELOW_FLOOR',
        reps_position_label: 'Reps abaixo da faixa',
      }),
    ).toEqual([
      { label: 'Esforço atingido', color: 'teal' },
      { label: 'Reps abaixo da faixa', color: 'gray' },
    ]);
    expect(savedSetStatusViews({ intensity_status: 'REACHED', reps_position: 'AT_TOP' })[1]).toEqual({
      label: motorConstants.intensity.reps_position_labels.AT_TOP,
      color: 'gray',
    });
  });

  it('abaixo do alvo em cinza; sem rótulo do servidor usa o gerado', () => {
    expect(savedSetStatusViews({ intensity_status: 'BELOW_TARGET' })).toEqual([
      { label: motorConstants.intensity.intensity_status_labels.BELOW_TARGET, color: 'gray' },
    ]);
  });

  it('RPE 10 negado só pela recuperação: esforço atingido + selo informativo azul, sem laranja', () => {
    const views = savedSetStatusViews({
      intensity_status: 'FAILURE_NOT_ALLOWED_REACHED_CEILING',
      intensity_status_label: 'Esforço atingido (falha não permitida)',
      safety_event: 'RPE_ABOVE_RECOVERY_TARGET',
      safety_event_label: 'RPE acima do alvo do dia',
    });
    expect(views).toEqual([
      { label: 'Esforço atingido (falha não permitida)', color: 'teal' },
      { label: 'RPE acima do alvo do dia', color: 'blue' },
    ]);
  });

  it('laranja só no alerta de segurança do par', () => {
    const views = savedSetStatusViews({
      intensity_status: 'ABOVE_SAFE_CEILING',
      safety_event: 'RPE_ABOVE_SAFE_CEILING',
    });
    expect(views).toEqual([{ label: motorConstants.intensity.intensity_status_labels.ABOVE_SAFE_CEILING, color: 'orange' }]);
  });

  it('sem status (treino livre, técnica) não mostra nada', () => {
    expect(savedSetStatusViews(null)).toEqual([]);
    expect(savedSetStatusViews({ intensity_status: null })).toEqual([]);
  });
});

describe('engineDecisionText', () => {
  it('"Ajuste de hoje: <rótulo do motor>"', () => {
    expect(engineDecisionText('HOLD')).toBe('Ajuste de hoje: Manter');
    expect(engineDecisionText('INCREASE_LOAD')).toBe('Ajuste de hoje: Aumentar carga');
  });

  it('sem decisão ou código desconhecido: nada', () => {
    expect(engineDecisionText(null)).toBeNull();
    expect(engineDecisionText('XYZ')).toBeNull();
  });
});

describe('setEvaluationCategoryView', () => {
  it('cor pela direção da carga', () => {
    expect(setEvaluationCategoryView('INCREASE_LOAD')).toEqual({ label: 'Aumentar carga', color: 'green' });
    expect(setEvaluationCategoryView('HOLD')).toEqual({ label: 'Manter', color: 'blue' });
    expect(setEvaluationCategoryView('DECREASE_LOAD')).toEqual({ label: 'Reduzir carga', color: 'orange' });
  });

  it('categoria antiga em cache vira selo neutro com o texto recebido', () => {
    expect(setEvaluationCategoryView('PROGREDIR')).toEqual({ label: 'PROGREDIR', color: 'gray' });
    expect(setEvaluationCategoryView(null)).toBeNull();
  });
});

describe('loadAdjustmentText', () => {
  it('corte pelo alvo do motor e pelo passo do equipamento, separados do RPE', () => {
    expect(loadAdjustmentText({ field: 'load_kg', from: 50, to: 42.5, reason: 'ENGINE_TARGET_LOAD' })).toBe(
      'Carga sugerida limitada ao alvo do motor: 50 kg → 42,5 kg',
    );
    expect(loadAdjustmentText({ field: 'load_kg', from: 46, to: 45, reason: 'EQUIPMENT_STEP' })).toBe(
      'Carga sugerida ajustada ao passo do equipamento: 46 kg → 45 kg',
    );
    expect(loadAdjustmentText({ field: 'load_kg', from: 1, to: 2, reason: 'OUTRO' })).toBeNull();
  });
});

describe('nextSetLoadView (carga da próxima série pelo motor)', () => {
  const reasons = motorConstants.next_set_load.reason_labels;

  it('direção e carga do backend, motivo pelo rótulo gerado quando o servidor não manda o seu', () => {
    const view = nextSetLoadView({
      direction: 'INCREASE_LOAD',
      direction_label: 'Aumentar carga',
      load_kg: 22.5,
      reason: 'NEXT_SET_REPS_ABOVE_LIMIT',
    });
    expect(view?.text).toBe('Próxima série: Aumentar carga → 22,5 kg');
    expect(view?.badge.color).toBe('green');
    expect(view?.reason).toBe(reasons.NEXT_SET_REPS_ABOVE_LIMIT);
  });

  it('rampa planejada: o rótulo detalhado do servidor vence o genérico', () => {
    const view = nextSetLoadView({
      direction: 'HOLD',
      load_kg: 20,
      reason: 'NEXT_SET_WITHIN_PLANNED_RAMP',
      reason_label: 'RPE dentro da rampa planejada (alvo 8 nesta série)',
      planned_rpe: 8,
    });
    expect(view?.text).toBe('Próxima série: Manter → 20 kg');
    expect(view?.reason).toBe('RPE dentro da rampa planejada (alvo 8 nesta série)');
    expect(reasons.NEXT_SET_WITHIN_PLANNED_RAMP).toBeTruthy();
  });

  it('manter em azul, reduzir em laranja; reduzir sem passo abaixo mostra só a direção', () => {
    expect(nextSetLoadView({ direction: 'HOLD', load_kg: 20, reason: 'NEXT_SET_WITHIN_RULE' })?.badge.color).toBe('blue');
    const reduzir = nextSetLoadView({ direction: 'DECREASE_LOAD', load_kg: null, reason: 'NEXT_SET_FAILURE_BEFORE_MIN_REPS' });
    expect(reduzir?.text).toBe('Próxima série: Reduzir carga');
    expect(reduzir?.badge.color).toBe('orange');
  });

  it('fora da regra (null) não mostra nada', () => {
    expect(nextSetLoadView(null)).toBeNull();
    expect(nextSetLoadView(undefined)).toBeNull();
  });

  it('ajuste da IA pela regra tem texto próprio', () => {
    expect(
      loadAdjustmentText({
        field: 'load_kg',
        from: 25,
        to: 20,
        reason: motorConstants.next_set_load.load_adjustment_reason_rule,
      }),
    ).toBe('Carga sugerida ajustada à regra da próxima série: 25 kg → 20 kg');
  });

  it('R1: regra cadastrada mostra o texto pronto do servidor; os campos novos são aditivos', () => {
    const block = motorConstants.next_set_load;
    expect(block.reason_rule_matched).toBe('NEXT_SET_RULE_MATCHED');
    expect(block.reasons).toContain(block.reason_rule_matched);
    expect(reasons[block.reason_rule_matched as keyof typeof reasons]).toBeTruthy();
    const view = nextSetLoadView({
      direction: 'INCREASE_LOAD',
      direction_label: 'Aumentar carga',
      load_kg: 25,
      reason: block.reason_rule_matched,
      reason_label: '1ª série · músculo descansado · reps > teto (12) · RPE < máximo → Aumentar 25% (no passo do equipamento)',
      rule_position: 1,
      rule_text: '1ª série · músculo descansado · reps > teto (12) · RPE < máximo → Aumentar 25% (no passo do equipamento)',
      ruleset_version: 3,
      magnitude: { kind: 'percent', value: 25 },
    });
    expect(view?.text).toBe('Próxima série: Aumentar carga → 25 kg');
    expect(view?.reason).toBe(
      '1ª série · músculo descansado · reps > teto (12) · RPE < máximo → Aumentar 25% (no passo do equipamento)',
    );
    // Sem o rótulo do servidor, o genérico do JSON gerado.
    expect(nextSetLoadView({ direction: 'HOLD', load_kg: 20, reason: block.reason_rule_matched })?.reason).toBe(
      reasons[block.reason_rule_matched as keyof typeof reasons],
    );
  });

  it('R1: o vocabulário, os limites e o conjunto padrão vêm do dono (sem cópia no FE)', () => {
    const block = motorConstants.next_set_load;
    expect(block.rule_vocabulary.set_positions.map((item) => item.value)).toEqual(['any', 'first', 'second', 'third_plus']);
    expect(block.rule_vocabulary.fatigue_conditions.map((item) => item.value)).toEqual([
      'any',
      'rested',
      'pre_fatigued',
      'heavily_fatigued',
    ]);
    expect(block.rule_vocabulary.actions.map((item) => item.label)).toEqual(
      block.rule_vocabulary.actions.map((item) => PROGRESSION_DECISION_LABELS[item.value]),
    );
    expect(block.rule_limits.max_rules).toBeGreaterThan(0);
    expect(block.default_ruleset.version).toBe(0);
    expect(block.default_ruleset.rules).toHaveLength(3);
    expect(block.default_ruleset.rules.map((rule) => rule.position)).toEqual([1, 2, 3]);
  });
});
