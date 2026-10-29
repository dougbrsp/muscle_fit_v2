import { describe, expect, it } from 'vitest';

import motorConstants from './motor-constants.generated.json';
import {
  collapsedSlotTotalSeconds,
  DEFAULT_ESTIMATED_REPS,
  EXEC_SECONDS_PER_REP,
  estimateSlotTime,
  formatRestSeconds, parseRepsTarget,
  REST_WHEN_UNDEFINED_SECONDS,
  type TimeEstimateItem,
} from './template-time-estimate';
import parity from './template-time-estimate.parity.json';

function item(overrides: Partial<TimeEstimateItem> = {}): TimeEstimateItem {
  return {
    item_kind: 'equipment',
    sets_count: null,
    reps_target: null,
    cardio_duration_minutes: null,
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

describe('estimateSlotTime', () => {
  it('trabalho: séries × (reps × 3 + descanso)', () => {
    const r = estimateSlotTime([item({ sets_count: 3, reps_target: '10', rest_seconds: 60 })]);
    expect(r.breakdown.work).toBe(3 * (30 + 60));
    expect(r.breakdown.execSeconds).toBe(90);
    expect(r.breakdown.restSeconds).toBe(180);
    expect(r.totalSeconds).toBe(270);
    expect(r.itemsWithoutRest).toBe(0);
  });

  it('aquecimento e ajuste usam os próprios reps e intervalos', () => {
    const r = estimateSlotTime([
      item({
        warmup_sets_count: 2,
        warmup_reps: 15,
        warmup_rest_seconds: 30,
        feeler_set_sets_count: 1,
        feeler_set_reps: 5,
        feeler_set_rest_seconds: 90,
      }),
    ]);
    expect(r.breakdown.warmup).toBe(2 * (45 + 30));
    expect(r.breakdown.feeler).toBe(1 * (15 + 90));
    expect(r.totalSeconds).toBe(150 + 105);
  });

  it('técnica: aplicações × duração, contada como descanso', () => {
    const r = estimateSlotTime([item({ advanced_technique_applications: 3, advanced_technique_duration_seconds: 10 })]);
    expect(r.breakdown.technique).toBe(30);
    expect(r.breakdown.restSeconds).toBe(30);
  });

  it('cardio: minutos × 60', () => {
    const r = estimateSlotTime([item({ item_kind: 'cardio', cardio_duration_minutes: 20, sets_count: 3 })]);
    expect(r.breakdown.cardio).toBe(1200);
    expect(r.totalSeconds).toBe(1200);
    expect(r.itemsWithoutRest).toBe(0);
  });

  it('exercício sem descanso conta 0 s e entra em itemsWithoutRest', () => {
    const r = estimateSlotTime([
      item({ sets_count: 3, reps_target: '10' }),
      item({ sets_count: 2, reps_target: '10', rest_seconds: 0 }),
      item({ sets_count: 2, reps_target: '10', rest_seconds: 45 }),
    ]);
    expect(r.breakdown.work).toBe(90 + 60 + 2 * (30 + 45));
    expect(r.itemsWithoutRest).toBe(2);
  });

  it('faixa de reps usa a média; pico de contração soma a cada rep', () => {
    const r = estimateSlotTime([item({ sets_count: 1, reps_target: '8-12', peak_contraction_seconds: 2, rest_seconds: 60 })]);
    expect(r.breakdown.work).toBe(10 * 5 + 60);
  });

  it('reps vazias assumem 10 e marcam estimado', () => {
    const r = estimateSlotTime([item({ sets_count: 1, rest_seconds: 60 })]);
    expect(r.breakdown.work).toBe(30 + 60);
    expect(r.hasEstimatedReps).toBe(true);
  });
});

describe('parseRepsTarget / formatRestSeconds', () => {
  it('interpreta número, faixa e vazio', () => {
    expect(parseRepsTarget('12')).toBe(12);
    expect(parseRepsTarget('6 a 9')).toBe(7.5);
    expect(parseRepsTarget('')).toBeNull();
    expect(parseRepsTarget(null)).toBeNull();
  });

  it('formata minutos e segundos', () => {
    expect(formatRestSeconds(3120)).toBe('52min');
    expect(formatRestSeconds(200)).toBe('3min 20s');
    expect(formatRestSeconds(0)).toBe('0s');
  });
});

describe('collapsedSlotTotalSeconds', () => {
  const items = [item({ sets_count: 3, reps_target: '10', rest_seconds: 60 })];

  it('é o mesmo total do slot expandido (estimateSlotTime)', () => {
    expect(collapsedSlotTotalSeconds({ slot_type: 'normal', items }, 'equipment_series')).toBe(
      estimateSlotTime(items).totalSeconds,
    );
  });

  it('não exibe fora do modo equipment_series', () => {
    expect(collapsedSlotTotalSeconds({ slot_type: 'normal', items }, 'percent_budget')).toBeNull();
  });

  it('não exibe em slot de descanso nem sem itens', () => {
    expect(collapsedSlotTotalSeconds({ slot_type: 'descanso', items }, 'equipment_series')).toBeNull();
    expect(collapsedSlotTotalSeconds({ slot_type: 'normal', items: [] }, 'equipment_series')).toBeNull();
  });

  it('não exibe quando o total estimado é zero', () => {
    expect(collapsedSlotTotalSeconds({ slot_type: 'normal', items: [item()] }, 'equipment_series')).toBeNull();
  });
});

// M76 (Lote 4c, E7): o dono é o backend (`periodization/time_estimate.py`);
// este arquivo é o espelho. Os casos e o resultado COMPLETO esperado vêm de
// `template-time-estimate.parity.json`, gerado pelo dono
// (`api/tests/unit/test_m76_lote1b_espelhos_fe.py --gravar`).
describe('paridade completa com o dono no backend (M76 Lote 4c)', () => {
  it.each(parity.cases.map((c) => [c.name, c] as const))('%s', (_name, c) => {
    const items = c.items.map((raw) => item(raw as Partial<TimeEstimateItem>));
    const defaultRest = 'default_rest_seconds' in c ? (c.default_rest_seconds as number) : undefined;
    const r = estimateSlotTime(items, defaultRest);
    const e = c.expected;
    expect(r).toEqual({
      totalSeconds: e.total_seconds,
      breakdown: {
        warmup: e.breakdown.warmup,
        feeler: e.breakdown.feeler,
        work: e.breakdown.work,
        technique: e.breakdown.technique,
        cardio: e.breakdown.cardio,
        execSeconds: e.breakdown.exec_seconds,
        restSeconds: e.breakdown.rest_seconds,
      },
      itemsWithoutRest: e.items_without_rest,
      hasEstimatedReps: e.has_estimated_reps,
    });
  });
});

describe('premissas lidas do dono', () => {
  it('não tem constante própria: usa motor-constants.generated.json', () => {
    expect(EXEC_SECONDS_PER_REP).toBe(motorConstants.time_estimate.exec_seconds_per_rep);
    expect(DEFAULT_ESTIMATED_REPS).toBe(motorConstants.time_estimate.default_estimated_reps);
    expect(REST_WHEN_UNDEFINED_SECONDS).toBe(motorConstants.time_estimate.rest_when_undefined_seconds);
  });
});
