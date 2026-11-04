import { describe, expect, it } from 'vitest';
import { ApiError } from './api';
import { editorFormFromMeal, itemsPayload } from './nutrition-meal-form';
import {
  clockTime,
  draftFromSnapshot,
  emptyQuickDraft,
  newMealClockTime,
  quickEntryChanged,
  quickEntryErrors,
  quickEntryPayload,
  quickMealPayload,
  type QuickEntryDraft,
} from './nutrition-quick-entry';
import type { Meal } from './nutrition-types';

function preenchido(extra: Partial<QuickEntryDraft['values']> = {}): QuickEntryDraft {
  const base = emptyQuickDraft();
  return {
    ...base,
    name: ' Barra de proteína ',
    values: { weight_g: 45, energy_kcal: 180, protein_g: '15', carbohydrates_g: 17, total_fat_g: 6, ...extra },
  };
}

const REFEICAO_RAPIDA = {
  id: 7,
  local_date: '2026-11-04',
  meal_slot: 'breakfast',
  eaten_at: '07:45',
  notes: null,
  items: [
    {
      id: 301,
      position: 0,
      food_id: null,
      portion_id: null,
      portion_label: null,
      label: 'Barra de proteína',
      quantity: 45,
      unit: 'g',
      amount: 45,
      basis: 'g',
      nature: 'USER_ENTERED',
      nutrients: { energy_kcal: 180, protein_g: 15, carbohydrates_g: 17, total_fat_g: 6, fiber_g: null, sodium_mg: null },
      limitations: [],
      food_source: 'quick_entry',
      citation: null,
      field_sources: [],
      quick_entry: {
        name: 'Barra de proteína',
        weight_g: 45,
        nutrients: { energy_kcal: 180, protein_g: 15, carbohydrates_g: 17, total_fat_g: 6, fiber_g: null, sodium_mg: null },
      },
    },
  ],
} as unknown as Meal;

describe('lançamento rápido: payload', () => {
  it('vazio não gera corpo; completo leva só os 6 campos, número como digitado', () => {
    expect(quickEntryPayload(emptyQuickDraft())).toBeNull();
    expect(quickEntryPayload(preenchido())).toEqual({
      name: 'Barra de proteína', weight_g: 45, energy_kcal: 180, protein_g: 15, carbohydrates_g: 17, total_fat_g: 6,
    });
    expect(quickEntryPayload(preenchido({ total_fat_g: '' }))).toBeNull();
  });

  it('zero é valor válido (o motor decide), campo vazio não', () => {
    expect(quickEntryPayload(preenchido({ total_fat_g: 0 }))?.total_fat_g).toBe(0);
  });

  it('POST: dia, refeição, hora e item quick_entry sem food_id, quantidade nem unidade', () => {
    const corpo = quickMealPayload({ date: '2026-11-04', slot: 'breakfast', eatenAt: '07:45', draft: preenchido() });
    expect(corpo).toEqual({
      local_date: '2026-11-04',
      meal_slot: 'breakfast',
      eaten_at: '07:45',
      notes: null,
      items: [{ quick_entry: { name: 'Barra de proteína', weight_g: 45, energy_kcal: 180, protein_g: 15, carbohydrates_g: 17, total_fat_g: 6 } }],
    });
    expect(Object.keys(corpo?.items[0] ?? {})).toEqual(['quick_entry']);
  });

  it('sem refeição escolhida não há corpo (nada é adivinhado); sem hora, eaten_at nulo', () => {
    expect(quickMealPayload({ date: '2026-11-04', slot: null, eatenAt: '07:45', draft: preenchido() })).toBeNull();
    expect(quickMealPayload({ date: '2026-11-04', slot: 'lunch', eatenAt: '', draft: preenchido() })?.eaten_at).toBeNull();
  });
});

describe('lançamento rápido: edição de item gravado', () => {
  it('o editor abre com os 6 valores do item e sem busca/porção', () => {
    const form = editorFormFromMeal(REFEICAO_RAPIDA);
    const item = form.items[0];
    expect(item.food_id).toBeNull();
    expect(item.usesEstimate).toBe(false);
    expect(item.quick?.name).toBe('Barra de proteína');
    expect(item.quick?.values).toMatchObject({ weight_g: 45, energy_kcal: 180, protein_g: 15, carbohydrates_g: 17, total_fat_g: 6 });
  });

  it('sem mudança manda só {id}', () => {
    const form = editorFormFromMeal(REFEICAO_RAPIDA);
    expect(itemsPayload(form.items)).toEqual([{ id: 301 }]);
  });

  it('com mudança manda quick_entry com o id', () => {
    const form = editorFormFromMeal(REFEICAO_RAPIDA);
    const quick = form.items[0].quick as QuickEntryDraft;
    const alterado = { ...form.items[0], quick: { ...quick, values: { ...quick.values, protein_g: 20 } } };
    expect(quickEntryChanged(alterado.quick)).toBe(true);
    expect(itemsPayload([alterado])).toEqual([
      { id: 301, quick_entry: { name: 'Barra de proteína', weight_g: 45, energy_kcal: 180, protein_g: 20, carbohydrates_g: 17, total_fat_g: 6 } },
    ]);
  });

  it('campo apagado na edição trava o corpo em vez de mandar 0', () => {
    const form = editorFormFromMeal(REFEICAO_RAPIDA);
    const quick = form.items[0].quick as QuickEntryDraft;
    expect(itemsPayload([{ ...form.items[0], quick: { ...quick, values: { ...quick.values, energy_kcal: '' } } }])).toBeNull();
  });

  it('o rascunho do valor gravado não conta como alterado', () => {
    const snapshot = REFEICAO_RAPIDA.items[0].quick_entry as NonNullable<Meal['items'][number]['quick_entry']>;
    expect(quickEntryChanged(draftFromSnapshot(snapshot))).toBe(false);
  });
});

describe('lançamento rápido: erros da API no campo certo', () => {
  const erro = (detail: unknown) => new ApiError('Refeição inválida', 400, detail);

  it('erros dos itens e da refeição vão para o campo ou para o geral', () => {
    const resultado = quickEntryErrors(
      erro({
        error: 'invalid_meal',
        errors: [{ code: 'invalid_clock_time', label: 'Hora inválida.' }],
        items: [
          {
            index: 0,
            errors: [
              { code: 'missing_field:energy_kcal', label: 'Informe as calorias.' },
              { code: 'quick_entry_weight_invalid', label: 'O peso deve ser maior que zero.' },
              { code: 'invalid_food_name', label: 'Nome inválido.' },
              { code: 'above_basis:protein_g', label: 'Proteína acima do peso.' },
            ],
          },
        ],
      }),
      'falhou',
    );
    expect(resultado.byField).toEqual({
      energy_kcal: 'Informe as calorias.',
      weight_g: 'O peso deve ser maior que zero.',
      name: 'Nome inválido.',
      protein_g: 'Proteína acima do peso.',
    });
    expect(resultado.general).toEqual(['Hora inválida.']);
  });

  it('erro sem estrutura mostra a mensagem geral', () => {
    expect(quickEntryErrors(new Error('rede'), 'Não foi possível salvar.')).toEqual({ byField: {}, general: ['Não foi possível salvar.'] });
  });
});

describe('newMealClockTime: hora inicial da refeição nova', () => {
  const agora = new Date(2026, 10, 4, 9, 5);
  it('só no dia de hoje usa o relógio', () => {
    expect(newMealClockTime('2026-11-04', '2026-11-04', agora)).toBe(clockTime(agora));
    expect(newMealClockTime('2026-11-03', '2026-11-04', agora)).toBe('');
    expect(newMealClockTime('2026-11-04', null, agora)).toBe('');
    expect(newMealClockTime('2026-11-04', undefined, agora)).toBe('');
  });
});

describe('clockTime: ciclo de 24 horas', () => {
  it('meia-noite é 00:xx, nunca 24:xx', () => {
    expect(clockTime(new Date(2026, 10, 4, 0, 7))).toBe('00:07');
    expect(clockTime(new Date(2026, 10, 4, 23, 59))).toBe('23:59');
  });
});
