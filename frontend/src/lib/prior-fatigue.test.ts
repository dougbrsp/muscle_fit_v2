import { describe, expect, it } from 'vitest';
import { itemActivationKey } from '@/lib/item-activation-summary';
import { buildItemPriorFatigueLookup, priorFatigueColor, priorFatigueLabel } from '@/lib/prior-fatigue';
import type {
  PreviewActivationResponse,
  PriorFatigue,
  WorkoutPlanTemplateSlotItemPayload,
  WorkoutPlanTemplateSlotPayload,
} from '@/lib/workout-plan-templates-types';

function hue(color: string): number {
  return Number(/^hsl\((\d+),/.exec(color)?.[1]);
}

const PRIOR = (overrides: Partial<PriorFatigue> = {}): PriorFatigue => ({
  muscle_group_id: 1,
  muscle_group_name: 'Peitoral',
  prior_sets: 0,
  level: 0,
  band: 'rested',
  band_label: 'descansado',
  ...overrides,
});

function equipment(mapping_id: number | null): WorkoutPlanTemplateSlotItemPayload {
  return { item_kind: 'equipment', mapping_id, muscle_group_id: 1, sets_count: 3 } as WorkoutPlanTemplateSlotItemPayload;
}

function slot(sequence: number, items: WorkoutPlanTemplateSlotItemPayload[]): WorkoutPlanTemplateSlotPayload {
  return { sequence, slot_type: 'normal', items } as WorkoutPlanTemplateSlotPayload;
}

describe('priorFatigueColor (apresentação contínua do nível da API)', () => {
  it('vai do verde (0) ao vermelho (1) passando pelo amarelo, sem degraus', () => {
    expect(hue(priorFatigueColor(0))).toBe(120);
    expect(hue(priorFatigueColor(0.5))).toBe(60);
    expect(hue(priorFatigueColor(1))).toBe(0);
    const hues = [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1].map((l) => hue(priorFatigueColor(l)));
    expect(new Set(hues).size).toBe(hues.length);
  });

  it('nível fora de 0..1 ou inválido fica no limite', () => {
    expect(priorFatigueColor(-3)).toBe(priorFatigueColor(0));
    expect(priorFatigueColor(7)).toBe(priorFatigueColor(1));
    expect(priorFatigueColor(Number.NaN)).toBe(priorFatigueColor(0));
  });
});

describe('priorFatigueLabel', () => {
  it('usa o grupo e o rótulo da faixa vindos da API', () => {
    expect(priorFatigueLabel(PRIOR({ band_label: 'muito fatigado' }))).toBe('Fadiga prévia no Peitoral: muito fatigado');
  });
});

describe('buildItemPriorFatigueLookup', () => {
  it('casa pela posição no corpo e devolve a chave do estado do builder', () => {
    const cardio = { item_kind: 'cardio', mapping_id: null, muscle_group_id: null } as WorkoutPlanTemplateSlotItemPayload;
    const slots = [slot(1, [cardio, equipment(10), equipment(20)])];
    const response = {
      cycle_length: 1,
      days: [],
      item_prior_fatigue: [
        { slot_sequence: 1, item_index: 0, item_kind: 'equipment', mapping_id: 10, muscle_group_id: 1, prior_fatigue: PRIOR() },
        {
          slot_sequence: 1,
          item_index: 1,
          item_kind: 'equipment',
          mapping_id: 20,
          muscle_group_id: 1,
          prior_fatigue: PRIOR({ level: 0.4, band: 'pre_fatigued', band_label: 'pré-fatigado' }),
        },
      ],
    } as PreviewActivationResponse;

    const lookup = buildItemPriorFatigueLookup(slots, response);
    // o cardio não vai no corpo: o item 1 do estado é o 0 do corpo
    expect(lookup.get(itemActivationKey(1, 1))?.band).toBe('rested');
    expect(lookup.get(itemActivationKey(1, 2))?.band).toBe('pre_fatigued');
    expect(lookup.has(itemActivationKey(1, 0))).toBe(false);
  });

  it('resposta de um corpo anterior (itens reordenados) não pinta o item errado', () => {
    const slots = [slot(1, [equipment(20), equipment(10)])];
    const response = {
      cycle_length: 1,
      days: [],
      item_prior_fatigue: [
        { slot_sequence: 1, item_index: 0, item_kind: 'equipment', mapping_id: 10, muscle_group_id: 1, prior_fatigue: PRIOR() },
        { slot_sequence: 1, item_index: 1, item_kind: 'equipment', mapping_id: 20, muscle_group_id: 1, prior_fatigue: PRIOR() },
      ],
    } as PreviewActivationResponse;
    expect(buildItemPriorFatigueLookup(slots, response).size).toBe(0);
  });

  it('sem o campo (resposta antiga) ou com prior_fatigue nulo, nada', () => {
    const slots = [slot(1, [equipment(10)])];
    expect(buildItemPriorFatigueLookup(slots, { cycle_length: 1, days: [] }).size).toBe(0);
    const nulo = {
      cycle_length: 1,
      days: [],
      item_prior_fatigue: [
        { slot_sequence: 1, item_index: 0, item_kind: 'equipment', mapping_id: 10, muscle_group_id: 1, prior_fatigue: null },
      ],
    } as PreviewActivationResponse;
    expect(buildItemPriorFatigueLookup(slots, nulo).size).toBe(0);
  });
});
