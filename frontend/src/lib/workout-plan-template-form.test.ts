import { describe, expect, it } from 'vitest';

import {
  countSlotSafetyWarnings,
  FREE_EXERCISE_PATCH,
  isFreeExerciseItem,
  hydrateSlots,
  isNormalSlotWithoutItems,
  itemMeasureSummary,
  MEASURE_SELECT_DATA,
  measureChangePatch,
  newItem,
  newSlot,
  pairSelectionPatch,
  safetyWarningsForItem,
  slotsToPayload,
  validateSlots,
  type ItemState,
  type SlotState,
} from './workout-plan-template-form';
import { ADVANCED_BLOCK_PRESENCE_FIELDS, DEFAULT_MEASURE, MEASURE_LIMITS, MEASURE_OPTIONS, MEASURE_VALUES } from './set-measures';
import motorConstants from './motor-constants.generated.json';
import { cardioMode } from './cardio-prescription';
import { gridPayload, resolveGrid } from './slot-grid';
import type {
  TemplateSafetyWarning,
  WorkoutPlanTemplateSlot,
  WorkoutPlanTemplateSlotItem,
} from './workout-plan-templates-types';

// Fixtures no formato do backend (GET do modelo / rascunho de periodização).
function apiItem(overrides: Partial<WorkoutPlanTemplateSlotItem>): WorkoutPlanTemplateSlotItem {
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

function apiSlot(overrides: Partial<WorkoutPlanTemplateSlot>): WorkoutPlanTemplateSlot {
  return {
    sequence: 1,
    name: 'Dia',
    weekday: null,
    slot_type: 'normal',
    priority_min_pct: null,
    priority_max_pct: null,
    items: [],
    ...overrides,
  };
}

// Estado local montado à mão (sem passar pela hidratação).
function item(overrides: Partial<ItemState>): ItemState {
  return { ...newItem(overrides.item_kind ?? 'muscle_group'), ...overrides };
}

function slot(overrides: Partial<SlotState>): SlotState {
  return { ...newSlot(1), name: 'A', ...overrides };
}

function warning(overrides: Partial<TemplateSafetyWarning>): TemplateSafetyWarning {
  return {
    slot_sequence: 1,
    slot_name: 'A',
    item_order: 1,
    muscle_group_id: null,
    mapping_id: null,
    equipment_id: null,
    code: 'RPE_ABOVE_SAFE_CEILING',
    message: 'RPE acima do teto',
    technique: null,
    max_safe_rpe: null,
    rpe_target: null,
    ...overrides,
  };
}

describe('newItem / newSlot', () => {
  it('preenche os padrões por tipo de item', () => {
    expect(newItem('muscle_group').sets_count).toBe(3);
    expect(newItem('equipment').sets_count).toBe(3);
    expect(newItem('cardio').sets_count).toBeNull();
    expect(newItem('cardio').cardio_duration_minutes).toBe(20);
    expect(newItem('equipment').cardio_duration_minutes).toBeNull();
    expect(newItem('equipment').rest_seconds).toBeNull();
    expect(newItem('equipment').prioritize).toBe(false);
  });

  it('nomeia o slot novo pela sequência e começa vazio', () => {
    const s = newSlot(4);
    expect(s.name).toBe('Dia 4');
    expect(s.slot_type).toBe('normal');
    expect(s.weekday).toBeNull();
    expect(s.items).toEqual([]);
  });

  it('gera localId distinto a cada item', () => {
    expect(newItem('cardio').localId).not.toBe(newItem('cardio').localId);
  });
});

describe('hydrateSlots -> slotsToPayload (round-trip)', () => {
  const fromApi: WorkoutPlanTemplateSlot[] = [
    apiSlot({
      sequence: 7,
      name: 'Pernas',
      weekday: 3,
      items: [
        apiItem({
          item_order: 1,
          item_kind: 'equipment',
          muscle_group_id: 10,
          mapping_id: 200,
          equipment_id: 300,
          sets_count: 7,
          reps_target: '8-12',
          rpe_target: '8',
          prioritize: true,
          advanced_technique: 'fst7',
          advanced_technique_applications: 3,
          advanced_technique_duration_seconds: 15,
          warmup_sets_count: 2,
          warmup_reps: 12,
          warmup_rest_seconds: 60,
          feeler_set_sets_count: 1,
          feeler_set_reps: 5,
          feeler_set_rest_seconds: 90,
          peak_contraction_seconds: 2,
          rest_seconds: 120,
        }),
        apiItem({
          item_order: 2,
          item_kind: 'equipment',
          muscle_group_id: 11,
          mapping_id: 201,
          equipment_id: 301,
          sets_count: 3,
          advanced_technique: 'drop_set',
          advanced_technique_applications: 2,
          advanced_technique_duration_seconds: 10,
        }),
      ],
    }),
    apiSlot({
      sequence: 2,
      name: 'Cardio',
      slot_type: 'descanso',
      items: [
        apiItem({
          item_order: 1,
          item_kind: 'cardio',
          cardio_duration_minutes: 30,
          // Lixo que o payload de cardio nunca envia.
          muscle_group_id: 99,
          sets_count: 4,
          reps_target: '10',
          rest_seconds: 45,
        }),
      ],
    }),
  ];

  it('ordena pelos `sequence` do backend e renumera como índice + 1', () => {
    const payload = slotsToPayload(hydrateSlots(fromApi));
    expect(payload.map((s) => [s.sequence, s.name])).toEqual([
      [1, 'Cardio'],
      [2, 'Pernas'],
    ]);
    expect(payload[1].weekday).toBe(3);
    expect(payload[1].items.map((i) => i.item_order)).toEqual([1, 2]);
  });

  it('M97 F3: o override do objetivo do item sobrevive ao round-trip; cardio vai sem', () => {
    const goal = 'qualquer-valor-da-api';
    const slots = [
      apiSlot({
        sequence: 1,
        items: [
          apiItem({ item_order: 1, item_kind: 'equipment', training_goal: goal }),
          apiItem({ item_order: 2, item_kind: 'muscle_group', training_goal: goal }),
          apiItem({ item_order: 3, item_kind: 'equipment' }),
          apiItem({ item_order: 4, item_kind: 'cardio', training_goal: goal }),
        ],
      }),
    ];
    const [slot] = slotsToPayload(hydrateSlots(slots));
    expect(slot.items.map((i) => i.training_goal)).toEqual([goal, goal, null, null]);
  });

  it('não altera a entrada ao ordenar', () => {
    hydrateSlots(fromApi);
    expect(fromApi.map((s) => s.sequence)).toEqual([7, 2]);
  });

  it('FST-7/GVT: aplicações e pausa vão zeradas; demais campos de equipamento passam', () => {
    const [, pernas] = slotsToPayload(hydrateSlots(fromApi));
    const fst7 = pernas.items[0];
    expect(fst7.advanced_technique).toBe('fst7');
    expect(fst7.sets_count).toBe(7);
    expect(fst7.advanced_technique_applications).toBeNull();
    expect(fst7.advanced_technique_duration_seconds).toBeNull();
    expect(fst7).toMatchObject({
      muscle_group_id: 10,
      mapping_id: 200,
      equipment_id: 300,
      reps_target: '8-12',
      rpe_target: '8',
      prioritize: true,
      warmup_sets_count: 2,
      warmup_reps: 12,
      warmup_rest_seconds: 60,
      feeler_set_sets_count: 1,
      feeler_set_reps: 5,
      feeler_set_rest_seconds: 90,
      peak_contraction_seconds: 2,
      rest_seconds: 120,
      cardio_duration_minutes: null,
      priority_min_pct: null,
      priority_max_pct: null,
    });
  });

  it('GVT também zera aplicações e pausa', () => {
    const s = slot({
      items: [
        item({
          item_kind: 'equipment',
          advanced_technique: 'gvt',
          advanced_technique_applications: 4,
          advanced_technique_duration_seconds: 20,
        }),
      ],
    });
    const [payloadSlot] = slotsToPayload([s]);
    expect(payloadSlot.items[0].advanced_technique_applications).toBeNull();
    expect(payloadSlot.items[0].advanced_technique_duration_seconds).toBeNull();
  });

  it('técnica com aplicações (drop-set) mantém aplicações e pausa', () => {
    const [, pernas] = slotsToPayload(hydrateSlots(fromApi));
    expect(pernas.items[1].advanced_technique).toBe('drop_set');
    expect(pernas.items[1].advanced_technique_applications).toBe(2);
    expect(pernas.items[1].advanced_technique_duration_seconds).toBe(10);
  });

  it('cardio só envia duração e priorizado; o resto vai nulo', () => {
    const [cardio] = slotsToPayload(hydrateSlots(fromApi));
    expect(cardio.slot_type).toBe('descanso');
    expect(cardio.items[0]).toMatchObject({
      item_order: 1,
      item_kind: 'cardio',
      cardio_duration_minutes: 30,
      muscle_group_id: null,
      sets_count: null,
      reps_target: null,
      rest_seconds: null,
      advanced_technique: null,
    });
  });

  it('rest_seconds ausente no backend (Modelo anterior à Fase 7) vira null', () => {
    const legacyItem = apiItem({ item_kind: 'equipment' }) as Partial<WorkoutPlanTemplateSlotItem>;
    delete legacyItem.rest_seconds;
    const [hydrated] = hydrateSlots([apiSlot({ items: [legacyItem as WorkoutPlanTemplateSlotItem] })]);
    expect(hydrated.items[0].rest_seconds).toBeNull();
  });

  it('grupo muscular envia faixa de prioridade e zera campos de equipamento', () => {
    const s = slot({
      items: [
        item({
          item_kind: 'muscle_group',
          muscle_group_id: 5,
          mapping_id: 1,
          equipment_id: 2,
          priority_min_pct: 10,
          priority_max_pct: 30,
          rest_seconds: 60,
          warmup_sets_count: 2,
        }),
      ],
    });
    const [payloadSlot] = slotsToPayload([s]);
    expect(payloadSlot.items[0]).toMatchObject({
      muscle_group_id: 5,
      mapping_id: null,
      equipment_id: null,
      sets_count: 3,
      priority_min_pct: 10,
      priority_max_pct: 30,
      rest_seconds: null,
      warmup_sets_count: null,
    });
  });

  it('hidratação gera localIds novos para slots e itens', () => {
    const hydrated = hydrateSlots(fromApi);
    const ids = [...hydrated.map((s) => s.localId), ...hydrated.flatMap((s) => s.items.map((i) => i.localId))];
    expect(new Set(ids).size).toBe(ids.length);
  });

  it('grade gravada volta no payload junto com os slots', () => {
    const three = [apiSlot({ sequence: 1 }), apiSlot({ sequence: 2 }), apiSlot({ sequence: 3 })];
    const hydrated = hydrateSlots(three);
    const grid = resolveGrid(2, 2, hydrated.length);
    expect({ ...gridPayload(grid, hydrated.length), slots: slotsToPayload(hydrated).map((s) => s.sequence) }).toEqual({
      grid_rows: 2,
      grid_cols: 2,
      slots: [1, 2, 3],
    });
  });

  it('grade NULL no GET sai como a padrão no save (sempre os dois campos)', () => {
    const five = [1, 2, 3, 4, 5].map((sequence) => apiSlot({ sequence }));
    const hydrated = hydrateSlots(five);
    expect(gridPayload(resolveGrid(null, null, hydrated.length), hydrated.length)).toEqual({
      grid_rows: 2,
      grid_cols: 4,
    });
  });
});

describe('validateSlots', () => {
  const okEquipment = () =>
    item({ item_kind: 'equipment', muscle_group_id: 1, equipment_id: 2, mapping_id: 3, sets_count: 3 });

  it('no máximo 8 slots (espelha o backend)', () => {
    const nine = Array.from({ length: 9 }, () => slot({}));
    expect(validateSlots(nine, 'percent_budget', null)).toBe('Um modelo pode ter no máximo 8 slots.');
    expect(validateSlots(nine.slice(0, 8), 'percent_budget', null)).toBeNull();
  });

  it('legado acima de 8 aceita até a quantidade já gravada', () => {
    const ten = Array.from({ length: 10 }, () => slot({}));
    expect(validateSlots(ten, 'percent_budget', null, { maxSlots: 10, grid: null })).toBeNull();
    expect(validateSlots([...ten, slot({})], 'percent_budget', null, { maxSlots: 10 })).toBe(
      'Um modelo pode ter no máximo 10 slots.',
    );
  });

  it('a grade precisa comportar todos os slots', () => {
    const three = [slot({}), slot({}), slot({})];
    expect(validateSlots(three, 'percent_budget', null, { grid: { rows: 1, cols: 2 } })).toBe(
      'A grade 1x2 comporta 2 slots, mas o modelo tem 3.',
    );
    expect(validateSlots(three, 'percent_budget', null, { grid: { rows: 1, cols: 3 } })).toBeNull();
  });

  it('exige ao menos 1 slot', () => {
    expect(validateSlots([], 'percent_budget', null)).toBe('Um modelo precisa de pelo menos 1 slot.');
  });

  it('aceita um modelo válido em cada modo', () => {
    expect(
      validateSlots([slot({ items: [item({ muscle_group_id: 1 })] })], 'percent_budget', null),
    ).toBeNull();
    expect(validateSlots([slot({ items: [okEquipment()] })], 'equipment_series', null)).toBeNull();
    expect(validateSlots([slot({ items: [] })], 'percent_budget', null)).toBeNull();
  });

  it('slot de descanso só aceita cardio', () => {
    const s = slot({ name: 'Folga', slot_type: 'descanso', items: [item({ muscle_group_id: 1 })] });
    expect(validateSlots([s], 'percent_budget', null)).toContain('Slot "Folga" (descanso)');
    const cardioOnly = slot({ slot_type: 'descanso', items: [item({ item_kind: 'cardio' })] });
    expect(validateSlots([cardioOnly], 'percent_budget', null)).toBeNull();
  });

  it('no máximo 3 itens priorizados por slot', () => {
    const items = [1, 2, 3, 4].map((id) => item({ muscle_group_id: id, prioritize: true }));
    expect(validateSlots([slot({ items })], 'percent_budget', null)).toContain('no máximo 3 itens');
    expect(validateSlots([slot({ items: items.slice(0, 3) })], 'percent_budget', null)).toBeNull();
  });

  it('grupo muscular não é permitido no modo equipment_series', () => {
    const s = slot({ items: [item({ muscle_group_id: 1 })] });
    expect(validateSlots([s], 'equipment_series', null)).toContain('não permite itens de grupo muscular');
  });

  it('grupo muscular precisa de grupo selecionado', () => {
    expect(validateSlots([slot({ items: [item({})] })], 'percent_budget', null)).toContain(
      'precisa de um grupo selecionado',
    );
  });

  it('equipamento só no modo equipment_series e completo', () => {
    expect(validateSlots([slot({ items: [okEquipment()] })], 'percent_budget', null)).toContain(
      'itens de equipamento só são permitidos',
    );
    // M101b: par pela metade é inválido; só o grupo = exercício livre.
    const missingMapping = { ...okEquipment(), mapping_id: null };
    expect(validateSlots([slot({ items: [missingMapping] })], 'equipment_series', null)).toContain(
      'par equipamento — exercício',
    );
    const missingEquipment = { ...okEquipment(), equipment_id: null };
    expect(validateSlots([slot({ items: [missingEquipment] })], 'equipment_series', null)).toContain(
      'par equipamento — exercício',
    );
    const freeExercise = { ...okEquipment(), equipment_id: null, mapping_id: null };
    expect(validateSlots([slot({ items: [freeExercise] })], 'equipment_series', null)).toBeNull();
    const noGroup = { ...freeExercise, muscle_group_id: null };
    expect(validateSlots([slot({ items: [noGroup] })], 'equipment_series', null)).toContain(
      'grupo muscular selecionado',
    );
    const zeroSets = { ...okEquipment(), sets_count: 0 };
    expect(validateSlots([slot({ items: [zeroSets] })], 'equipment_series', null)).toContain(
      'ao menos 1 série de trabalho',
    );
  });

  it('cardio precisa da duração mínima do dono', () => {
    const s = slot({ items: [item({ item_kind: 'cardio', cardio_duration_minutes: 0 })] });
    expect(validateSlots([s], 'percent_budget', null)).toContain(
      `duração de pelo menos ${motorConstants.cardio_prescription.limits.cardio_duration_minutes.min} min`,
    );
  });

  it('faixa de prioridade exige min e max juntos e min <= max', () => {
    const onlyMin = slot({ items: [item({ muscle_group_id: 1, priority_min_pct: 10 })] });
    expect(validateSlots([onlyMin], 'percent_budget', null)).toContain('preenchidos juntos');
    const inverted = slot({ items: [item({ muscle_group_id: 1, priority_min_pct: 50, priority_max_pct: 10 })] });
    expect(validateSlots([inverted], 'percent_budget', null)).toContain('mínima não pode ser maior');
  });

  it('com Meta de Ativação vinculada, Min%/Max% do grupo muscular são obrigatórios', () => {
    const s = slot({ items: [item({ muscle_group_id: 1 })] });
    expect(validateSlots([s], 'percent_budget', '7')).toContain('obrigatório quando o modelo tem Meta de Ativação');
    const filled = slot({ items: [item({ muscle_group_id: 1, priority_min_pct: 10, priority_max_pct: 20 })] });
    expect(validateSlots([filled], 'percent_budget', '7')).toBeNull();
    // Cardio não usa faixa de prioridade.
    const cardio = slot({ items: [item({ item_kind: 'cardio' })] });
    expect(validateSlots([cardio], 'percent_budget', '7')).toBeNull();
  });

  it('devolve o primeiro erro encontrado, na ordem dos slots', () => {
    const bad1 = slot({ name: 'Primeiro', items: [item({})] });
    const bad2 = slot({ name: 'Segundo', items: [item({ item_kind: 'cardio', cardio_duration_minutes: null })] });
    expect(validateSlots([bad1, bad2], 'percent_budget', null)).toContain('Slot "Primeiro"');
  });
});

describe('safetyWarningsForItem / countSlotSafetyWarnings', () => {
  const s = slot({
    items: [
      item({ item_kind: 'equipment', muscle_group_id: 1, equipment_id: 10, mapping_id: 100 }),
      item({ item_kind: 'equipment', muscle_group_id: 2, equipment_id: 20, mapping_id: 200 }),
      item({ item_kind: 'cardio' }),
    ],
  });

  it('sem avisos, conta zero', () => {
    expect(countSlotSafetyWarnings([], 0, s)).toBe(0);
    expect(safetyWarningsForItem([], 0, 0, s.items[0])).toEqual([]);
  });

  it('casa por slot_sequence/item_order (1-based) e confirma a identidade do item', () => {
    const all = [
      warning({ slot_sequence: 1, item_order: 1, mapping_id: 100, equipment_id: 10, muscle_group_id: 1 }),
      // Posição certa, identidade errada (item foi trocado desde o save).
      warning({ slot_sequence: 1, item_order: 2, mapping_id: 999 }),
      // Outro slot.
      warning({ slot_sequence: 2, item_order: 1 }),
    ];
    expect(safetyWarningsForItem(all, 0, 0, s.items[0])).toHaveLength(1);
    expect(safetyWarningsForItem(all, 0, 1, s.items[1])).toEqual([]);
    expect(countSlotSafetyWarnings(all, 0, s)).toBe(1);
    // O mesmo slot na 2ª posição do ciclo casa só o aviso de slot_sequence 2.
    expect(countSlotSafetyWarnings(all, 1, s)).toBe(1);
  });

  it('conta itens com aviso, não avisos', () => {
    const all = [
      warning({ item_order: 1, code: 'RPE_ABOVE_SAFE_CEILING' }),
      warning({ item_order: 1, code: 'MODEL_TECHNIQUE_INVALID' }),
      warning({ item_order: 3 }),
    ];
    expect(safetyWarningsForItem(all, 0, 0, s.items[0])).toHaveLength(2);
    expect(countSlotSafetyWarnings(all, 0, s)).toBe(2);
  });

  it('campos de identidade nulos no aviso não restringem o casamento', () => {
    const all = [warning({ item_order: 2, mapping_id: null, equipment_id: null, muscle_group_id: null })];
    expect(countSlotSafetyWarnings(all, 0, s)).toBe(1);
  });
});

describe('isNormalSlotWithoutItems', () => {
  it('avisa no slot normal sem nenhum item', () => {
    expect(isNormalSlotWithoutItems({ slot_type: 'normal', items: [] })).toBe(true);
  });

  it('não avisa no slot de descanso, mesmo vazio', () => {
    expect(isNormalSlotWithoutItems({ slot_type: 'descanso', items: [] })).toBe(false);
  });

  it('não avisa no slot normal com itens (inclusive só cardio)', () => {
    expect(isNormalSlotWithoutItems({ slot_type: 'normal', items: [newItem('equipment')] })).toBe(false);
    expect(isNormalSlotWithoutItems({ slot_type: 'normal', items: [newItem('cardio')] })).toBe(false);
  });

  it('retrato do Modelo 28: só os slots 4 e 8 ("Descanso", normais e vazios) são avisados', () => {
    const slots: SlotState[] = [1, 2, 3, 4, 5, 6, 7, 8].map((sequence) => ({
      ...newSlot(sequence),
      items: sequence === 4 || sequence === 8 ? [] : [newItem('equipment')],
    }));
    const avisados = slots.flatMap((slot, index) => (isNormalSlotWithoutItems(slot) ? [index + 1] : []));
    expect(avisados).toEqual([4, 8]);
  });
});

// Lote T2: o editor ainda não edita a medida da série (T4), mas salvar o Modelo
// pelo editor atual não pode apagar a medida por tempo / máximo gravada.
describe('medida da série no round-trip do editor (Lote T2)', () => {
  it('preserva medida, tempo por série e janela do item de equipamento', () => {
    const [hydrated] = hydrateSlots([
      apiSlot({
        items: [
          apiItem({
            item_kind: 'equipment',
            muscle_group_id: 1,
            mapping_id: 2,
            equipment_id: 3,
            sets_count: 3,
            measure: MEASURE_VALUES.duration,
            duration_seconds_target: 60,
          }),
          apiItem({
            item_order: 2,
            item_kind: 'equipment',
            muscle_group_id: 1,
            mapping_id: 4,
            equipment_id: 5,
            sets_count: 2,
            measure: MEASURE_VALUES.max_reps,
            time_window_seconds: 30,
          }),
        ],
      }),
    ]);
    const [payload] = slotsToPayload([hydrated]);
    expect(payload.items[0]).toMatchObject({
      measure: MEASURE_VALUES.duration,
      duration_seconds_target: 60,
      time_window_seconds: null,
    });
    expect(payload.items[1]).toMatchObject({
      measure: MEASURE_VALUES.max_reps,
      duration_seconds_target: null,
      time_window_seconds: 30,
    });
  });

  it('item sem medida (API antiga) e item novo vão sem medida (= repetições)', () => {
    const [hydrated] = hydrateSlots([apiSlot({ items: [apiItem({ sets_count: 3 })] })]);
    const [payload] = slotsToPayload([hydrated, slot({ items: [newItem('equipment')] })]);
    expect(payload.items[0].measure).toBeNull();
    expect(slotsToPayload([slot({ items: [newItem('equipment')] })])[0].items[0].measure).toBeNull();
  });

  it('fora do item de equipamento a medida não vai no payload', () => {
    const [payload] = slotsToPayload([
      slot({ items: [item({ item_kind: 'muscle_group', measure: MEASURE_VALUES.duration, duration_seconds_target: 60 })] }),
    ]);
    expect(payload.items[0]).toMatchObject({ measure: null, duration_seconds_target: null, time_window_seconds: null });
  });
});

// Lote T4: o card de equipamento edita a medida. Vocabulário e limites vêm do
// espelho (`set-measures.ts`); aqui só a forma do estado do editor.
describe('medida da série no editor (Lote T4)', () => {
  const equipmentItem = (overrides: Partial<ItemState> = {}) =>
    item({
      item_kind: 'equipment',
      muscle_group_id: 1,
      mapping_id: 2,
      equipment_id: 3,
      sets_count: 3,
      reps_target: '8-12',
      rpe_target: '8',
      advanced_technique: 'drop_set',
      advanced_technique_applications: 2,
      advanced_technique_duration_seconds: 10,
      warmup_sets_count: 2,
      warmup_reps: 12,
      warmup_rest_seconds: 60,
      feeler_set_sets_count: 1,
      feeler_set_reps: 5,
      feeler_set_rest_seconds: 90,
      peak_contraction_seconds: 2,
      ...overrides,
    });
  const advancedCleared = {
    reps_target: null,
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
  };

  it('o Select usa as opções e rótulos do dono, na ordem dele', () => {
    expect(MEASURE_SELECT_DATA).toEqual(MEASURE_OPTIONS.map((o) => ({ value: o.value, label: o.label })));
  });

  it('trocar para tempo limpa reps, bloco avançado e janela; marca como escolhida', () => {
    const after = { ...equipmentItem({ time_window_seconds: 30 }), ...measureChangePatch(MEASURE_VALUES.duration) };
    expect(after).toMatchObject({
      ...advancedCleared,
      measure: MEASURE_VALUES.duration,
      time_window_seconds: null,
      measure_touched: true,
      sets_count: 3,
      rpe_target: '8',
    });
  });

  it('fora de reps limpa todo campo que o dono conta como bloco avançado', () => {
    for (const kind of [MEASURE_VALUES.duration, MEASURE_VALUES.max_reps]) {
      const patch = measureChangePatch(kind) as Record<string, unknown>;
      for (const field of ADVANCED_BLOCK_PRESENCE_FIELDS) {
        expect(patch, field).toHaveProperty(field, null);
      }
    }
  });

  it('trocar para máximo de reps limpa reps, bloco avançado e tempo por série', () => {
    const after = { ...equipmentItem({ duration_seconds_target: 60 }), ...measureChangePatch(MEASURE_VALUES.max_reps) };
    expect(after).toMatchObject({ ...advancedCleared, measure: MEASURE_VALUES.max_reps, duration_seconds_target: null });
  });

  it('voltar para reps limpa só os campos de tempo; o resto do item fica', () => {
    const before = equipmentItem({ measure: MEASURE_VALUES.duration, duration_seconds_target: 60, reps_target: null });
    expect({ ...before, ...measureChangePatch(DEFAULT_MEASURE) }).toMatchObject({
      measure: DEFAULT_MEASURE,
      duration_seconds_target: null,
      time_window_seconds: null,
      advanced_technique: 'drop_set',
      warmup_reps: 12,
    });
  });

  it('payload de item por tempo sai sem os campos que o servidor recusaria', () => {
    const edited = { ...equipmentItem(), ...measureChangePatch(MEASURE_VALUES.duration), duration_seconds_target: 45 };
    const [payload] = slotsToPayload([slot({ items: [edited] })]);
    expect(payload.items[0]).toMatchObject({
      ...advancedCleared,
      measure: MEASURE_VALUES.duration,
      duration_seconds_target: 45,
      time_window_seconds: null,
    });
    expect(payload.items[0]).not.toHaveProperty('measure_touched');
  });

  describe('exercício livre (M101b)', () => {
    it('o item livre é só o grupo, sem par', () => {
      const withGroup = { ...newItem('equipment'), muscle_group_id: 3 };
      expect(isFreeExerciseItem(withGroup)).toBe(true);
      expect(isFreeExerciseItem({ ...withGroup, mapping_id: 8, equipment_id: 7 })).toBe(false);
      expect(isFreeExerciseItem({ ...withGroup, mapping_id: 8 })).toBe(false);
      expect(isFreeExerciseItem(newItem('equipment'))).toBe(false); // sem grupo
      expect(isFreeExerciseItem({ ...newItem('cardio'), muscle_group_id: 3 })).toBe(false);
    });

    it('a opção do motor limpa o par e mantém o grupo', () => {
      const pair = { ...newItem('equipment'), muscle_group_id: 3, equipment_id: 7, mapping_id: 8 };
      expect(isFreeExerciseItem({ ...pair, ...FREE_EXERCISE_PATCH })).toBe(true);
    });
  });

  describe('pré-seleção pelo default_measure do par', () => {
    it('item novo pega a medida sugerida do par e limpa o incompatível', () => {
      const fresh = { ...newItem('equipment'), reps_target: '10' };
      const patch = pairSelectionPatch(fresh, 7, 8, MEASURE_VALUES.duration);
      expect(patch).toMatchObject({ equipment_id: 7, mapping_id: 8, measure: MEASURE_VALUES.duration, reps_target: null });
      expect(patch.measure_touched).toBeUndefined();
    });

    it('par sem sugestão (ou desconhecida) volta à medida padrão do dono enquanto o usuário não mexeu', () => {
      const auto = { ...newItem('equipment'), measure: MEASURE_VALUES.duration, duration_seconds_target: 60 };
      expect(pairSelectionPatch(auto, 7, 8, null)).toMatchObject({ measure: DEFAULT_MEASURE, duration_seconds_target: null });
      expect(pairSelectionPatch(auto, 7, 8, 'xpto')).toMatchObject({ measure: DEFAULT_MEASURE });
    });

    it('mesma medida: só troca o par, sem limpar nada', () => {
      const fresh: ItemState = { ...newItem('equipment'), reps_target: '10' };
      expect(pairSelectionPatch(fresh, 7, 8, DEFAULT_MEASURE)).toEqual({
        equipment_id: 7,
        mapping_id: 8,
      });
    });

    it('depois de o usuário escolher a medida, o par não muda mais nada', () => {
      const touched = { ...newItem('equipment'), ...measureChangePatch(DEFAULT_MEASURE) };
      expect(pairSelectionPatch(touched, 7, 8, MEASURE_VALUES.duration)).toEqual({ equipment_id: 7, mapping_id: 8 });
    });

    it('item hidratado (salvo ou da Periodização) conta como escolhido', () => {
      const [hydrated] = hydrateSlots([
        apiSlot({ items: [apiItem({ sets_count: 3, measure: DEFAULT_MEASURE, reps_target: '10' })] }),
      ]);
      expect(pairSelectionPatch(hydrated.items[0], 7, 8, MEASURE_VALUES.duration)).toEqual({
        equipment_id: 7,
        mapping_id: 8,
      });
    });
  });

  it('round-trip de item reps sem tocar a medida: payload idêntico ao que veio', () => {
    const reps = apiItem({
      item_kind: 'equipment',
      muscle_group_id: 1,
      mapping_id: 2,
      equipment_id: 3,
      sets_count: 4,
      reps_target: '8-12',
      rpe_target: '8',
      advanced_technique: 'drop_set',
      advanced_technique_applications: 2,
      advanced_technique_duration_seconds: 10,
      warmup_sets_count: 1,
      warmup_reps: 12,
      warmup_rest_seconds: 60,
      peak_contraction_seconds: 2,
      rest_seconds: 90,
      measure: DEFAULT_MEASURE,
      duration_seconds_target: null,
      time_window_seconds: null,
      // M97 F3: a API devolve o override (null = herda) e o payload o ecoa.
      training_goal: null,
    });
    const [payload] = slotsToPayload(hydrateSlots([apiSlot({ items: [reps] })]));
    expect(payload.items[0]).toEqual(reps);
  });

  it('resumo do card recolhido: só fora de reps, com séries e unidade do dono', () => {
    const maxLabel = MEASURE_OPTIONS.find((o) => o.value === MEASURE_VALUES.max_reps)!.shortLabel;
    const base = { sets_count: 3, duration_seconds_target: null, time_window_seconds: null };
    expect(itemMeasureSummary(equipmentItem())).toBeNull();
    expect(itemMeasureSummary({ ...base, measure: MEASURE_VALUES.duration, duration_seconds_target: 60 })).toBe('3 × 60 s');
    expect(itemMeasureSummary({ ...base, measure: MEASURE_VALUES.max_reps, time_window_seconds: 30 })).toBe(
      `3 × ${maxLabel} em 30 s`,
    );
    expect(itemMeasureSummary({ ...base, measure: MEASURE_VALUES.max_reps })).toBe(`3 × ${maxLabel}`);
  });

  describe('validateSlots: forma da medida com os limites do dono', () => {
    const check = (overrides: Partial<ItemState>) =>
      validateSlots([slot({ items: [equipmentItem({ ...advancedCleared, ...overrides })] })], 'equipment_series', null);

    it('tempo exige o tempo por série dentro dos limites', () => {
      expect(check({ measure: MEASURE_VALUES.duration })).toMatch(/tempo por série/);
      expect(
        check({ measure: MEASURE_VALUES.duration, duration_seconds_target: MEASURE_LIMITS.durationSecondsMax + 1 }),
      ).toMatch(/tempo por série/);
      expect(check({ measure: MEASURE_VALUES.duration, duration_seconds_target: MEASURE_LIMITS.durationSecondsMin })).toBeNull();
    });

    it('janela do máximo é opcional, mas dentro dos limites quando preenchida', () => {
      expect(check({ measure: MEASURE_VALUES.max_reps })).toBeNull();
      expect(check({ measure: MEASURE_VALUES.max_reps, time_window_seconds: MEASURE_LIMITS.timeWindowSecondsMin - 1 })).toMatch(
        /janela/,
      );
      expect(check({ measure: MEASURE_VALUES.max_reps, time_window_seconds: MEASURE_LIMITS.timeWindowSecondsMax })).toBeNull();
    });
  });
});

// M98 C0: a prescrição de cardio (colunas da 095) ainda não tem tela, mas o PUT
// é replace-all — o editor atual repassa os campos para não apagá-los.
describe('prescrição de cardio no editor atual (M98 C0)', () => {
  const hiit = {
    cardio_mode: 'hiit',
    cardio_warmup_minutes: 10,
    cardio_cooldown_minutes: 5,
    cardio_target_zone: null,
    hiit_low_seconds: 90,
    hiit_high_seconds: 30,
    hiit_low_zone: 2,
    hiit_high_zone: 4,
    hiit_rounds: 8,
  };

  it('hidrata e devolve no payload os campos novos do item de cardio', () => {
    const slots = hydrateSlots([
      apiSlot({ items: [apiItem({ item_kind: 'cardio', muscle_group_id: null, cardio_duration_minutes: null, ...hiit })] }),
    ]);
    const payload = slotsToPayload(slots)[0].items[0];
    expect(payload).toMatchObject(hiit);
    expect(payload.cardio_duration_minutes).toBeNull();
  });

  it('item que não é cardio não leva os campos novos', () => {
    const payload = slotsToPayload([slot({ items: [item({ item_kind: 'muscle_group', muscle_group_id: 1 })] })])[0]
      .items[0];
    expect('cardio_mode' in payload).toBe(false);
  });

  it('cardio novo nasce com a duração padrão do dono', () => {
    expect(newItem('cardio').cardio_duration_minutes).toBe(
      motorConstants.cardio_prescription.defaults.continuous.cardio_duration_minutes,
    );
  });

  it('HIIT não exige a duração do Normal (a validação completa é do backend)', () => {
    const s = slot({ items: [item({ item_kind: 'cardio', cardio_duration_minutes: null, ...hiit })] });
    expect(validateSlots([s], 'percent_budget', null)).toBeNull();
  });

  it('M98 C1: cardio novo nasce Normal com todos os padrões do dono', () => {
    const novo = newItem('cardio');
    expect(novo.cardio_mode).toBe(motorConstants.cardio_prescription.default_mode);
    expect(novo).toMatchObject(motorConstants.cardio_prescription.defaults.continuous);
    expect(novo.hiit_rounds).toBeNull();
    expect('cardio_mode' in newItem('equipment')).toBe(false);
  });

  it('M98 C1: cardio antigo (só duração) abre como Normal e volta igual no save', () => {
    const [hydrated] = hydrateSlots([
      apiSlot({ items: [apiItem({ item_kind: 'cardio', muscle_group_id: null, cardio_duration_minutes: 25 })] }),
    ]);
    const antigo = hydrated.items[0];
    expect(cardioMode(antigo)).toBe('continuous');
    const payload = slotsToPayload([hydrated])[0].items[0];
    // Nada inventado: o modo e os campos novos seguem vazios (forma canônica de
    // antes da 095 -> o PUT sem mudança não abre versão).
    expect(payload).toMatchObject({
      cardio_duration_minutes: 25,
      cardio_mode: null,
      cardio_warmup_minutes: null,
      cardio_cooldown_minutes: null,
      cardio_target_zone: null,
    });
  });
});
