import { describe, expect, it } from 'vitest';

import {
  buildItemActivationLookup,
  formatActivationNumber,
  formatItemActivationLine,
  itemActivationKey,
  summarizeItemActivation,
} from './item-activation-summary';
import type {
  PreviewActivationGroupTotal,
  PreviewActivationItem,
  PreviewActivationItemContribution,
  PreviewActivationResponse,
  WorkoutPlanTemplateSlotItemPayload,
  WorkoutPlanTemplateSlotPayload,
} from './workout-plan-templates-types';

const TOTALS: PreviewActivationGroupTotal[] = [
  { muscle_group_id: 3, muscle_group: 'Costas miolo/densidade', total_equivalent: 10 },
  { muscle_group_id: 4, muscle_group: 'Bíceps', total_equivalent: 5 },
  { muscle_group_id: 5, muscle_group: 'Core', total_equivalent: 2 },
  { muscle_group_id: 7, muscle_group: 'Glúteos', total_equivalent: 3 },
  { muscle_group_id: 6, muscle_group: 'Lombar', total_equivalent: 1.5 },
];

function c(
  muscle_group: string,
  role: PreviewActivationItemContribution['role'],
  value: number,
): PreviewActivationItemContribution {
  return { muscle_group_id: null, muscle_group, role, value };
}

const REMADA: PreviewActivationItemContribution[] = [
  c('Glúteos', 'stabilizer', 0.5),
  c('Costas miolo/densidade', 'primary', 5),
  c('Lombar', 'stabilizer', 1),
  c('Bíceps', 'secondary', 2.5),
  c('Core', 'stabilizer', 1.2),
];

describe('formatação pt-BR', () => {
  it('uma casa decimal com vírgula', () => {
    expect(formatActivationNumber(5)).toBe('5,0');
    expect(formatActivationNumber(1.25)).toMatch(/^1,[23]$/);
    expect(formatActivationNumber(12.345)).toBe('12,3');
  });
});

describe('summarizeItemActivation + formatItemActivationLine (M11)', () => {
  it('agrupa principal | secundários | Estabilizadores, maior valor primeiro', () => {
    const summary = summarizeItemActivation(REMADA, TOTALS);
    expect(formatItemActivationLine(summary)).toBe(
      'Costas miolo/densidade: 5,0/10,0 | Bíceps: 2,5/5,0 | Estabilizadores: Core: 1,2/2,0 - Lombar: 1,0/1,5 - Glúteos: 0,5/3,0',
    );
  });

  it('vários secundários separados por " - " e sem segmento vazio', () => {
    const summary = summarizeItemActivation(
      [c('Bíceps', 'secondary', 1), c('Core', 'secondary', 2), c('Costas miolo/densidade', 'primary', 3)],
      TOTALS,
    );
    expect(formatItemActivationLine(summary)).toBe('Costas miolo/densidade: 3,0/10,0 | Core: 2,0/2,0 - Bíceps: 1,0/5,0');
  });

  it('Modo A (grupo muscular): só o principal', () => {
    expect(formatItemActivationLine(summarizeItemActivation([c('Bíceps', 'primary', 3)], TOTALS))).toBe('Bíceps: 3,0/5,0');
  });

  it('grupo sem total, valor zerado ou lista vazia não inventa número', () => {
    expect(summarizeItemActivation([c('Peitoral', 'primary', 3)], TOTALS)).toBeNull();
    expect(summarizeItemActivation([c('Bíceps', 'primary', 0)], TOTALS)).toBeNull();
    expect(summarizeItemActivation([], TOTALS)).toBeNull();
    expect(formatItemActivationLine(null)).toBe('');
  });

  it('numerador nunca aparece acima do denominador (arredondamento do servidor)', () => {
    const summary = summarizeItemActivation([c('Core', 'primary', 2.01)], TOTALS);
    expect(summary?.primary[0].value).toBe(2);
    expect(formatItemActivationLine(summary)).toBe('Core: 2,0/2,0');
  });
});

function item(over: Partial<WorkoutPlanTemplateSlotItemPayload> = {}): WorkoutPlanTemplateSlotItemPayload {
  return {
    item_order: 1,
    item_kind: 'equipment',
    muscle_group_id: 3,
    mapping_id: 100,
    equipment_id: 9,
    sets_count: 4,
    reps_target: null,
    rpe_target: null,
    prioritize: false,
    cardio_duration_minutes: null,
    priority_min_pct: null,
    priority_max_pct: null,
    advanced_technique: null,
    advanced_technique_applications: null,
    advanced_technique_duration_seconds: null,
    warmup_sets_count: null,
    warmup_reps: null,
    warmup_rest_seconds: null,
    feeler_set_sets_count: null,
    feeler_set_reps: null,
    feeler_set_rest_seconds: null,
    peak_contraction_seconds: null,
    rest_seconds: null,
    ...over,
  };
}

function slot(sequence: number, items: WorkoutPlanTemplateSlotItemPayload[]): WorkoutPlanTemplateSlotPayload {
  return {
    sequence,
    name: `Slot ${sequence}`,
    weekday: null,
    slot_type: 'normal',
    priority_min_pct: null,
    priority_max_pct: null,
    items,
  };
}

function responseItem(
  slot_sequence: number,
  item_index: number,
  over: Partial<PreviewActivationItem> = {},
): PreviewActivationItem {
  return {
    slot_sequence,
    item_index,
    item_order: item_index + 1,
    item_kind: 'equipment',
    mapping_id: 100,
    muscle_group_id: null,
    occurrences: 4,
    contributions: REMADA,
    ...over,
  };
}

function response(items: PreviewActivationItem[]): PreviewActivationResponse {
  return { cycle_length: 2, days: [], window_days: 7, items, group_totals: TOTALS };
}

describe('buildItemActivationLookup (M11)', () => {
  it('reconstrói o índice do estado pulando os itens que não vão no corpo', () => {
    const slots = [
      slot(1, [
        item({ item_kind: 'cardio', mapping_id: null, muscle_group_id: null }),
        item({ mapping_id: null }), // equipamento ainda sem exercício
        item({ mapping_id: 100 }),
      ]),
    ];
    const lookup = buildItemActivationLookup(slots, response([responseItem(1, 0)]));
    expect([...lookup.keys()]).toEqual([itemActivationKey(1, 2)]);
    expect(formatItemActivationLine(lookup.get(itemActivationKey(1, 2)) ?? null)).toContain(
      'Costas miolo/densidade: 5,0/10,0',
    );
  });

  it('resposta antiga (estrutura mudou) não empresta número de outro item', () => {
    // resposta do corpo anterior: índice 0 era o mapping 200; agora é o 100
    const slots = [slot(1, [item({ mapping_id: 100 })])];
    expect(buildItemActivationLookup(slots, response([responseItem(1, 0, { mapping_id: 200 })])).size).toBe(0);
  });

  it('só as séries mudaram: mantém o valor anterior enquanto o novo carrega', () => {
    const slots = [slot(1, [item({ mapping_id: 100, sets_count: 6 })])];
    expect(buildItemActivationLookup(slots, response([responseItem(1, 0)])).has(itemActivationKey(1, 0))).toBe(true);
  });

  it('Modo A casa pelo grupo muscular', () => {
    const slots = [slot(2, [item({ item_kind: 'muscle_group', mapping_id: null, muscle_group_id: 4 })])];
    const echoed = responseItem(2, 0, {
      item_kind: 'muscle_group',
      mapping_id: null,
      muscle_group_id: 4,
      contributions: [c('Bíceps', 'primary', 3)],
    });
    const lookup = buildItemActivationLookup(slots, response([echoed]));
    expect(formatItemActivationLine(lookup.get(itemActivationKey(2, 0)) ?? null)).toBe('Bíceps: 3,0/5,0');
    // outro grupo no mesmo lugar: nada
    const other = [slot(2, [item({ item_kind: 'muscle_group', mapping_id: null, muscle_group_id: 5 })])];
    expect(buildItemActivationLookup(other, response([echoed])).size).toBe(0);
  });

  it('slot de descanso e resposta sem detalhamento não produzem linha', () => {
    const rest = [{ ...slot(1, [item()]), slot_type: 'descanso' as const }];
    expect(buildItemActivationLookup(rest, response([responseItem(1, 0)])).size).toBe(0);
    const old: PreviewActivationResponse = { cycle_length: 1, days: [], window_days: 7 };
    expect(buildItemActivationLookup([slot(1, [item()])], old).size).toBe(0);
    expect(buildItemActivationLookup([slot(1, [item()])], undefined).size).toBe(0);
  });
});
