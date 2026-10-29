// PR-18 — nenhum caminho do app gera ficha sem Modelo de Treino.
//
// O que se prova aqui é a regra que antes estava duplicada (e divergente) nas
// duas telas de geração: o payload SEMPRE leva `template_id`, nunca leva
// `plan_type`/`split_day`, e o botão de gerar só existe com modelo escolhido.

import { describe, expect, it } from 'vitest';
import {
  AUTO_SLOT_VALUE,
  buildGeneratePayload,
  canGenerateDraft,
  localPlanTime,
} from './workout-plan-generate-payload';
import { legacyPlanTypeLabel, planTypeDisplayLabel } from './workout-plans-types';

describe('canGenerateDraft', () => {
  it('exige um modelo selecionado', () => {
    expect(canGenerateDraft('')).toBe(false);
    expect(canGenerateDraft(null)).toBe(false);
    expect(canGenerateDraft(undefined)).toBe(false);
    expect(canGenerateDraft('12')).toBe(true);
  });
});

describe('buildGeneratePayload', () => {
  it('manda o template_id e o slot escolhido', () => {
    const payload = buildGeneratePayload({
      templateId: '7',
      templateSlotSequence: '3',
      gymId: '2',
      planDate: '2026-09-20',
      planTime: '19:30',
      goal: 'hipertrofia',
      minRestSeconds: 45,
      maxRestSeconds: 120,
    });

    expect(payload.template_id).toBe(7);
    expect(payload.template_slot_sequence).toBe(3);
    expect(payload.gym_id).toBe(2);
    expect(payload.plan_date).toBe('2026-09-20');
    expect(payload.plan_time).toBe('19:30');
    expect(payload.goal).toBe('hipertrofia');
    expect(payload.min_rest_seconds).toBe(45);
    expect(payload.max_rest_seconds).toBe(120);
  });

  it('slot automático vira null (o backend resolve o próximo do ciclo)', () => {
    const payload = buildGeneratePayload({ templateId: '7', templateSlotSequence: AUTO_SLOT_VALUE });
    expect(payload.template_slot_sequence).toBeNull();
  });

  it('campos vazios viram null e os descansos têm padrão', () => {
    const payload = buildGeneratePayload({ templateId: '7', gymId: '', planDate: '', goal: '' });
    expect(payload.gym_id).toBeNull();
    expect(payload.plan_date).toBeNull();
    expect(payload.goal).toBeNull();
    expect(payload.min_rest_seconds).toBe(30);
    expect(payload.max_rest_seconds).toBe(90);
  });

  it('não existe mais plan_type/split_day no payload de geração', () => {
    const payload = buildGeneratePayload({ templateId: '7' });
    expect(Object.keys(payload).sort()).toEqual(
      [
        'goal',
        'gym_id',
        'gym_name',
        'max_rest_seconds',
        'min_rest_seconds',
        'plan_date',
        'plan_time',
        'template_id',
        'template_slot_sequence',
      ].sort(),
    );
  });

  it('sem modelo é erro de programação — a tela nunca deve chegar aqui', () => {
    expect(() => buildGeneratePayload({ templateId: '' })).toThrow(/Modelo de Treino/);
  });
});

describe('legacyPlanTypeLabel', () => {
  it('mantém legível a ficha antiga (compatibilidade §12)', () => {
    expect(legacyPlanTypeLabel('abc')).toBe('ABC por alças musculares');
    expect(legacyPlanTypeLabel('full_body')).toBe('Full Body (IA)');
    expect(legacyPlanTypeLabel('qualquer_coisa')).toBe('qualquer_coisa');
    expect(legacyPlanTypeLabel(null)).toBe('-');
  });
});

describe('planTypeDisplayLabel', () => {
  it('ficha de Modelo mostra o nome do Modelo, nunca o rótulo legado', () => {
    // Bug real: Modelo novo grava o default plan_type='full_body' e a ficha
    // aparecia como "Full Body (IA)".
    expect(planTypeDisplayLabel({ plan_type: 'full_body', template_id: 28, template_name: 'PPL 5x' })).toBe('PPL 5x');
  });

  it('ficha de Modelo sem nome (Modelo removido) cai em "Modelo #id"', () => {
    expect(planTypeDisplayLabel({ plan_type: 'full_body', template_id: 28, template_name: null })).toBe('Modelo #28');
    expect(planTypeDisplayLabel({ plan_type: 'full_body', template_id: 28, template_name: '  ' })).toBe('Modelo #28');
  });

  it('ficha sem Modelo (pré-PR-18) mantém o rótulo legado', () => {
    expect(planTypeDisplayLabel({ plan_type: 'abc', template_id: null, template_name: null })).toBe('ABC por alças musculares');
    expect(planTypeDisplayLabel({ plan_type: 'full_body' })).toBe('Full Body (IA)');
  });
});

describe('localPlanTime', () => {
  it('formata a hora de parede local como HH:MM com zero à esquerda', () => {
    // Construtor local (não UTC): 7h05 no fuso do dispositivo.
    expect(localPlanTime(new Date(2026, 9, 5, 7, 5, 59))).toBe('07:05');
    expect(localPlanTime(new Date(2026, 9, 5, 23, 0, 0))).toBe('23:00');
  });

  it('usa a hora local, não a hora UTC de toISOString', () => {
    const d = new Date(2026, 9, 5, 18, 42);
    expect(localPlanTime(d)).toBe('18:42');
    if (d.getTimezoneOffset() !== 0) {
      expect(localPlanTime(d)).not.toBe(d.toISOString().slice(11, 16));
    }
  });

  it('vai para plan_time no payload de geração', () => {
    const planTime = localPlanTime(new Date(2026, 9, 5, 6, 30));
    expect(buildGeneratePayload({ templateId: '3', planTime }).plan_time).toBe('06:30');
  });
});
