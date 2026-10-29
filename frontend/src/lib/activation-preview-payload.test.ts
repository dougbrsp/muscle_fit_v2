import { describe, expect, it } from 'vitest';

import { buildActivationPreviewPayload, serializeActivationPreviewPayload } from './activation-preview-payload';
import type {
  WorkoutPlanTemplateSlotItemPayload,
  WorkoutPlanTemplateSlotPayload,
} from './workout-plan-templates-types';

function item(over: Partial<WorkoutPlanTemplateSlotItemPayload> = {}): WorkoutPlanTemplateSlotItemPayload {
  return {
    item_order: 1,
    item_kind: 'muscle_group',
    muscle_group_id: 3,
    mapping_id: null,
    equipment_id: null,
    sets_count: 4,
    reps_target: '8-12',
    rpe_target: '7-8',
    prioritize: false,
    cardio_duration_minutes: null,
    priority_min_pct: 30,
    priority_max_pct: 50,
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

function slot(over: Partial<WorkoutPlanTemplateSlotPayload> = {}): WorkoutPlanTemplateSlotPayload {
  return {
    sequence: 1,
    name: 'Peito',
    weekday: 1,
    slot_type: 'normal',
    priority_min_pct: 60,
    priority_max_pct: 70,
    items: [item(), item({ item_order: 2, muscle_group_id: 5, sets_count: 3 })],
    ...over,
  };
}

const key = (slots: WorkoutPlanTemplateSlotPayload[], mode?: 'percent_budget' | 'equipment_series') =>
  serializeActivationPreviewPayload(slots, mode);

describe('buildActivationPreviewPayload (M10)', () => {
  it('reproduz o problema antigo: o corpo cru muda a cada tecla no nome do slot', () => {
    const before = JSON.stringify({ slots: [slot({ name: 'Pei' })] });
    const after = JSON.stringify({ slots: [slot({ name: 'Peito e tri' })] });
    expect(before).not.toBe(after);
  });

  it('digitar um nome longo, letra a letra, nunca muda o corpo (nenhuma requisição)', () => {
    const base = key([slot({ name: '' })]);
    const typed = 'Peito, ombro e triceps - dia pesado';
    for (let i = 1; i <= typed.length; i += 1) {
      expect(key([slot({ name: typed.slice(0, i) })])).toBe(base);
    }
  });

  it('campos fora do cálculo não mudam o corpo', () => {
    const base = key([slot()]);
    const variants: WorkoutPlanTemplateSlotPayload[] = [
      slot({ weekday: 5 }),
      slot({ priority_min_pct: 10, priority_max_pct: 20 }),
      slot({ items: [item({ reps_target: '15', rpe_target: '9', prioritize: true, priority_min_pct: 1 }), item({ item_order: 2, muscle_group_id: 5, sets_count: 3 })] }),
      slot({ items: [item({ advanced_technique: 'drop_set', warmup_sets_count: 2, peak_contraction_seconds: 2 }), item({ item_order: 2, muscle_group_id: 5, sets_count: 3 })] }),
      // adicionar um item de cardio não altera a ativação por grupo
      slot({
        items: [
          item(),
          item({ item_order: 2, muscle_group_id: 5, sets_count: 3 }),
          item({ item_order: 3, item_kind: 'cardio', muscle_group_id: null, sets_count: null, cardio_duration_minutes: 20 }),
        ],
      }),
    ];
    for (const v of variants) expect(key([v])).toBe(base);
  });

  it('campos do cálculo mudam o corpo', () => {
    const base = key([slot()]);
    const changed: WorkoutPlanTemplateSlotPayload[] = [
      slot({ items: [item({ sets_count: 5 }), item({ item_order: 2, muscle_group_id: 5, sets_count: 3 })] }),
      slot({ items: [item({ muscle_group_id: 9 }), item({ item_order: 2, muscle_group_id: 5, sets_count: 3 })] }),
      slot({ slot_type: 'descanso' }),
      slot({ items: [item()] }),
      slot({ sequence: 2 }),
    ];
    for (const v of changed) expect(key([v])).not.toBe(base);
    expect(key([slot()], 'equipment_series')).not.toBe(base);
  });

  it('modo equipment_series: usa mapping_id e ignora equipment_id/reps', () => {
    const eq = (over: Partial<WorkoutPlanTemplateSlotItemPayload>) =>
      slot({ items: [item({ item_kind: 'equipment', muscle_group_id: 3, mapping_id: 11, equipment_id: 7, sets_count: 3, ...over })] });
    const base = key([eq({})], 'equipment_series');
    expect(key([eq({ equipment_id: 8, reps_target: '5', muscle_group_id: 4 })], 'equipment_series')).toBe(base);
    expect(key([eq({ mapping_id: 12 })], 'equipment_series')).not.toBe(base);
    expect(key([eq({ sets_count: 4 })], 'equipment_series')).not.toBe(base);
  });

  it('itens que não contribuem e slot descanso saem do corpo', () => {
    const payload = buildActivationPreviewPayload([
      slot({
        items: [
          item({ muscle_group_id: null }),
          item({ item_kind: 'cardio', muscle_group_id: null, cardio_duration_minutes: 20 }),
          // equipamento sem par e sem grupo; par pela metade (só equipamento)
          item({ item_kind: 'equipment', muscle_group_id: null, mapping_id: null }),
          item({ item_kind: 'equipment', mapping_id: null, equipment_id: 4 }),
        ],
      }),
      slot({ sequence: 2, slot_type: 'descanso' }),
    ]);
    expect(payload.slots[0].items).toEqual([]);
    expect(payload.slots[1].items).toEqual([]);
    expect(payload.slots[0].name).toBe('Dia 1');
  });

  it('exercício livre (M101b) entra com o grupo; o par completo, sem', () => {
    const payload = buildActivationPreviewPayload(
      [
        slot({
          items: [
            item({ item_kind: 'equipment', mapping_id: null, equipment_id: null, sets_count: 4 }),
            item({ item_kind: 'equipment', mapping_id: 8, equipment_id: 7, sets_count: 3 }),
          ],
        }),
      ],
      'equipment_series',
    );
    expect(payload.slots[0].items.map((i) => [i.muscle_group_id, i.mapping_id, i.sets_count])).toEqual([
      [3, null, 4],
      [null, 8, 3],
    ]);
  });
});

describe('buildActivationPreviewPayload - tipo de treino (modo orçamento)', () => {
  it('modo percent_budget com tipo de treino leva training_type_id e a faixa % dos itens de grupo', () => {
    const payload = buildActivationPreviewPayload([slot()], 'percent_budget', 12);
    expect(payload.training_type_id).toBe(12);
    expect(payload.slots[0].items[0]).toMatchObject({ priority_min_pct: 30, priority_max_pct: 50 });
    // a faixa passa a mudar o corpo (o volume do item vem dela)
    const changed = slot({ items: [item({ priority_min_pct: 10 }), item({ item_order: 2, muscle_group_id: 5, sets_count: 3 })] });
    expect(serializeActivationPreviewPayload([changed], 'percent_budget', 12)).not.toBe(
      serializeActivationPreviewPayload([slot()], 'percent_budget', 12),
    );
    // e o tipo de treino também (queryKey)
    expect(serializeActivationPreviewPayload([slot()], 'percent_budget', 13)).not.toBe(
      serializeActivationPreviewPayload([slot()], 'percent_budget', 12),
    );
  });

  it('modo default (undefined) conta como percent_budget', () => {
    expect(buildActivationPreviewPayload([slot()], undefined, 12).training_type_id).toBe(12);
  });

  it('sem tipo de treino, o corpo fica idêntico ao de antes', () => {
    expect(key([slot()], 'percent_budget')).toBe(serializeActivationPreviewPayload([slot()], 'percent_budget', null));
    expect(buildActivationPreviewPayload([slot()], 'percent_budget', null)).not.toHaveProperty('training_type_id');
  });

  it('modo equipment_series ignora o tipo de treino (corpo inalterado)', () => {
    const eq = slot({ items: [item({ item_kind: 'equipment', mapping_id: 11, sets_count: 3 })] });
    expect(serializeActivationPreviewPayload([eq], 'equipment_series', 12)).toBe(key([eq], 'equipment_series'));
  });
});
