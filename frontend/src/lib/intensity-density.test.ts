import { describe, expect, it } from 'vitest';

import { FALLBACK_ADVANCED_TECHNIQUES } from './advanced-techniques';
import parity from './intensity-density.parity.json';
import {
  aggregateIntensityDensityByGroup,
  buildIntensityDensityByGroup,
  combinePosition,
  computeItemIntensityDensity,
  densitySubindices,
  directionOf,
  formatIndexPercent,
  itemIntensity,
  parseRpeRamp,
  type IntensityDensityEntry,
  type IntensityDensityItemInput,
} from './intensity-density';
import type {
  PreviewActivationItemContribution,
  PreviewActivationResponse,
  WorkoutPlanTemplateSlotItemPayload,
  WorkoutPlanTemplateSlotPayload,
} from './workout-plan-templates-types';

const CATALOG = FALLBACK_ADVANCED_TECHNIQUES;

function input(overrides: Partial<IntensityDensityItemInput> = {}): IntensityDensityItemInput {
  return {
    sets_count: null,
    reps_target: null,
    rpe_target: null,
    rest_seconds: null,
    peak_contraction_seconds: null,
    advanced_technique: null,
    ...overrides,
  };
}

function payloadItem(overrides: Partial<WorkoutPlanTemplateSlotItemPayload>): WorkoutPlanTemplateSlotItemPayload {
  return {
    item_order: 1,
    item_kind: 'equipment',
    muscle_group_id: null,
    mapping_id: null,
    equipment_id: null,
    sets_count: null,
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
    ...overrides,
  };
}

function contribution(
  muscle_group: string,
  role: PreviewActivationItemContribution['role'],
  value: number,
): PreviewActivationItemContribution {
  return { muscle_group_id: null, muscle_group, role, value };
}

describe('parseRpeRamp (porte de parse_rpe_ramp)', () => {
  it('lê rampa, escalar, vírgula e vazio', () => {
    expect(parseRpeRamp('8-9-10')).toEqual([8, 9, 10]);
    expect(parseRpeRamp('9')).toEqual([9]);
    expect(parseRpeRamp('8,5')).toEqual([8.5]);
    expect(parseRpeRamp(9)).toEqual([9]);
    expect(parseRpeRamp('')).toEqual([]);
    expect(parseRpeRamp(null)).toEqual([]);
  });
});

describe('intensidade', () => {
  it('RPE 10 é intensidade máxima; RPE 7 é zero', () => {
    expect(itemIntensity(input({ sets_count: 3, rpe_target: '10' }), CATALOG)).toBe(1);
    expect(itemIntensity(input({ sets_count: 3, rpe_target: '7' }), CATALOG)).toBe(0);
  });

  it('rampa conta série a série e repete o último degrau', () => {
    // 8, 9, 10 → (0,3 + 0,7 + 1,0) / 3
    expect(itemIntensity(input({ sets_count: 3, rpe_target: '8-9-10' }), CATALOG)).toBeCloseTo(2 / 3, 6);
    // 4 séries com rampa de 2: 8, 9, 9, 9
    expect(itemIntensity(input({ sets_count: 4, rpe_target: '8-9' }), CATALOG)).toBeCloseTo((0.3 + 0.7 * 3) / 4, 6);
  });

  it('técnica de falha faz a última série contar RPE 10', () => {
    const base = input({ sets_count: 3, rpe_target: '8' });
    expect(itemIntensity(base, CATALOG)).toBeCloseTo(0.3, 6);
    expect(itemIntensity({ ...base, advanced_technique: 'drop_set' }, CATALOG)).toBeCloseTo((0.3 * 2 + 1) / 3, 6);
    expect(itemIntensity({ ...base, advanced_technique: 'forced_reps' }, CATALOG)).toBeCloseTo((0.3 * 2 + 1) / 3, 6);
    // GVT não exige falha
    expect(itemIntensity({ ...base, advanced_technique: 'gvt' }, CATALOG)).toBeCloseTo(0.3, 6);
  });

  it('sem RPE o componente fica ausente', () => {
    expect(itemIntensity(input({ sets_count: 3 }), CATALOG)).toBeNull();
    const item = computeItemIntensityDensity(input({ sets_count: 3, reps_target: '15' }), CATALOG);
    expect(item.intensity).toBeNull();
    // só densidade: p = 1 − D
    expect(item.position).toBeCloseTo(1 - (item.density as number), 6);
  });
});

describe('densidade/volume', () => {
  it('reps altas aumentam a densidade (ponto médio da faixa)', () => {
    expect(densitySubindices(input({ reps_target: '6' })).reps).toBe(0);
    expect(densitySubindices(input({ reps_target: '15' })).reps).toBe(1);
    expect(densitySubindices(input({ reps_target: '20' })).reps).toBe(1);
    expect(densitySubindices(input({ reps_target: '8-12' })).reps).toBeCloseTo(4 / 9, 6);
  });

  it('pico de contração: 0 s → 0, 5 s → 1', () => {
    expect(densitySubindices(input({ peak_contraction_seconds: 0 })).peak).toBe(0);
    expect(densitySubindices(input({ peak_contraction_seconds: 5 })).peak).toBe(1);
    expect(densitySubindices(input({})).peak).toBeNull();
  });

  it('descanso curto é mais denso; longo é zero; nulo fica de fora', () => {
    expect(densitySubindices(input({ rest_seconds: 45 })).rest).toBe(1);
    expect(densitySubindices(input({ rest_seconds: 30 })).rest).toBe(1);
    expect(densitySubindices(input({ rest_seconds: 180 })).rest).toBe(0);
    expect(densitySubindices(input({ rest_seconds: 240 })).rest).toBe(0);
    expect(densitySubindices(input({ rest_seconds: 112.5 })).rest).toBeCloseTo(0.5, 6);
    expect(densitySubindices(input({})).rest).toBeNull();
  });

  it('séries: 2 → 0, 5 → 1', () => {
    expect(densitySubindices(input({ sets_count: 2 })).sets).toBe(0);
    expect(densitySubindices(input({ sets_count: 5 })).sets).toBe(1);
  });
});

describe('posição e direção', () => {
  it('combina I e D; um lado só usa I ou 1 − D', () => {
    expect(combinePosition(1, 0)).toBe(1);
    expect(combinePosition(0, 1)).toBe(0);
    expect(combinePosition(0.5, 0.5)).toBe(0.5);
    expect(combinePosition(0.8, null)).toBe(0.8);
    expect(combinePosition(null, 0.8)).toBeCloseTo(0.2, 6);
    expect(combinePosition(null, null)).toBeNull();
  });

  it('faixa de equilíbrio ±0,1', () => {
    expect(directionOf(0.55)).toBe('balanced');
    expect(directionOf(0.7)).toBe('intensity');
    expect(directionOf(0.3)).toBe('density');
    expect(directionOf(null)).toBeNull();
  });

  it('item pesado e até a falha aponta para Intensidade; leve e denso para Densidade', () => {
    const heavy = computeItemIntensityDensity(
      input({ sets_count: 2, rpe_target: '10', reps_target: '6', rest_seconds: 180, peak_contraction_seconds: 0 }),
      CATALOG,
    );
    expect(heavy.position).toBe(1);
    const dense = computeItemIntensityDensity(
      input({ sets_count: 5, rpe_target: '7', reps_target: '15', rest_seconds: 45, peak_contraction_seconds: 5 }),
      CATALOG,
    );
    expect(dense.position).toBe(0);
  });

  it('formata percentual', () => {
    expect(formatIndexPercent(0.734)).toBe('73%');
    expect(formatIndexPercent(null)).toBe('—');
  });
});

describe('buildIntensityDensityByGroup', () => {
  // Slot 1: item 0 é cardio (fora do corpo do preview), item 1 pesado (Peitoral
  // primário, Tríceps secundário), item 2 denso (Tríceps primário).
  const slots: WorkoutPlanTemplateSlotPayload[] = [
    {
      sequence: 1,
      name: 'A',
      weekday: null,
      slot_type: 'normal',
      priority_min_pct: null,
      priority_max_pct: null,
      items: [
        payloadItem({ item_kind: 'cardio', cardio_duration_minutes: 10 }),
        payloadItem({
          mapping_id: 11,
          sets_count: 2,
          rpe_target: '10',
          reps_target: '6',
          rest_seconds: 180,
          peak_contraction_seconds: 0,
          warmup_sets_count: 4,
          warmup_reps: 20,
        }),
        payloadItem({
          mapping_id: 22,
          sets_count: 5,
          rpe_target: '7',
          reps_target: '15',
          rest_seconds: 45,
          peak_contraction_seconds: 5,
        }),
      ],
    },
  ];

  const response: PreviewActivationResponse = {
    cycle_length: 1,
    window_days: 7,
    days: [],
    items: [
      {
        slot_sequence: 1,
        item_index: 0, // posição no CORPO (o cardio não vai)
        item_order: 1,
        item_kind: 'equipment',
        mapping_id: 11,
        muscle_group_id: null,
        occurrences: 7,
        contributions: [
          contribution('Peitoral', 'primary', 14),
          contribution('Tríceps', 'secondary', 7),
          contribution('Core', 'stabilizer', 3),
        ],
      },
      {
        slot_sequence: 1,
        item_index: 1,
        item_order: 2,
        item_kind: 'equipment',
        mapping_id: 22,
        muscle_group_id: null,
        occurrences: 7,
        contributions: [contribution('Tríceps', 'primary', 21)],
      },
    ],
  };

  it('pondera por contribution.value (secundária pesa menos) e ignora estabilizador', () => {
    const rows = buildIntensityDensityByGroup(slots, response, CATALOG);
    expect(rows.map((r) => r.muscleGroup)).toEqual(['Tríceps', 'Peitoral']);

    const peitoral = rows.find((r) => r.muscleGroup === 'Peitoral')!;
    expect(peitoral.position).toBe(1);
    expect(peitoral.label).toBe('→ Intensidade');

    // Tríceps: pesado (p=1) com peso 7 + denso (p=0) com peso 21 → 7/28
    const triceps = rows.find((r) => r.muscleGroup === 'Tríceps')!;
    expect(triceps.weight).toBe(28);
    expect(triceps.position).toBeCloseTo(0.25, 6);
    expect(triceps.direction).toBe('density');
    expect(triceps.label).toBe('← Densidade/Volume');
    expect(triceps.intensity).toBeCloseTo(0.25, 6);
    expect(triceps.density).toBeCloseTo(0.75, 6);

    expect(rows.some((r) => r.muscleGroup === 'Core')).toBe(false);
  });

  it('grupo sem insumo aparece como "sem dados"', () => {
    const bare: WorkoutPlanTemplateSlotPayload[] = [
      { ...slots[0], items: [payloadItem({ item_kind: 'muscle_group', muscle_group_id: 5 })] },
    ];
    const rows = buildIntensityDensityByGroup(
      bare,
      {
        cycle_length: 1,
        days: [],
        items: [
          {
            slot_sequence: 1,
            item_index: 0,
            item_order: 1,
            item_kind: 'muscle_group',
            mapping_id: null,
            muscle_group_id: 5,
            occurrences: 1,
            contributions: [contribution('Costas', 'primary', 3)],
          },
        ],
      },
      CATALOG,
    );
    expect(rows).toHaveLength(1);
    expect(rows[0].position).toBeNull();
    expect(rows[0].label).toBe('sem dados');
  });

  it('resposta de outra estrutura (eco não bate) ou vazia não produz linhas', () => {
    const swapped = { ...response, items: response.items!.map((i) => ({ ...i, mapping_id: 99 })) };
    expect(buildIntensityDensityByGroup(slots, swapped, CATALOG)).toEqual([]);
    expect(buildIntensityDensityByGroup(slots, undefined, CATALOG)).toEqual([]);
  });
});

// Paridade com o DONO (`api/app/domain/intensity_density.py`): casos e
// resultados gerados por `api/tests/unit/test_m76_lote1b_espelhos_fe.py --gravar`.
describe('paridade com o dono (intensity-density.parity.json)', () => {
  const catalog = parity.catalog as unknown as Parameters<typeof itemIntensity>[1];

  it.each(parity.ramps.map((c) => [JSON.stringify(c.input), c] as const))('rampa %s', (_nome, c) => {
    expect(parseRpeRamp(c.input as string | number | null)).toEqual(c.expected);
  });

  it.each(parity.items.map((c) => [c.name, c] as const))('item: %s', (_nome, c) => {
    const computed = computeItemIntensityDensity(c.item as unknown as IntensityDensityItemInput, catalog);
    expectClose(computed.intensity, c.expected.intensity);
    expectClose(computed.density, c.expected.density);
    expectClose(computed.position, c.expected.position);
    for (const key of ['reps', 'peak', 'rest', 'sets'] as const) {
      expectClose(computed.subindices[key], c.expected.subindices[key]);
    }
    expect(directionOf(computed.position)).toBe(c.expected.direction);
  });

  it.each(parity.groups.map((c) => [c.name, c] as const))('grupo: %s', (_nome, c) => {
    const rows = aggregateIntensityDensityByGroup(c.entries as unknown as IntensityDensityEntry[], catalog);
    expect(rows.map((r) => r.muscleGroup)).toEqual(c.expected.map((r) => r.muscle_group));
    rows.forEach((row, i) => {
      const esperado = c.expected[i];
      expect(row.muscleGroupId).toBe(esperado.muscle_group_id);
      expectClose(row.weight, esperado.weight);
      expectClose(row.position, esperado.position);
      expectClose(row.intensity, esperado.intensity);
      expectClose(row.density, esperado.density);
      for (const key of ['reps', 'peak', 'rest', 'sets'] as const) {
        expectClose(row.subindices[key], esperado.subindices[key]);
      }
      expect(row.direction).toBe(esperado.direction);
    });
  });
});

function expectClose(actual: number | null, expected: number | null | undefined): void {
  if (expected == null) {
    expect(actual).toBeNull();
    return;
  }
  expect(actual).not.toBeNull();
  expect(actual as number).toBeCloseTo(expected, 12);
}
