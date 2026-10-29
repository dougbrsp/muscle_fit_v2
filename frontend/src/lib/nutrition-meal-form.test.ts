import { describe, expect, it } from 'vitest';

import { PORTION_UNITS } from './nutrition-constants';
import {
  editorFormFromMeal,
  editorItemFromFood,
  itemsPayload,
  mealCreatePayload,
  mealPayload,
  unitUsesPortion,
  type MealEditorForm,
} from './nutrition-meal-form';
import type { Meal } from './nutrition-types';

const ARROZ = { id: 3, name: 'Arroz, tipo 1, cozido', basis: 'g' };
// Campos da leitura por IA (L5b) num item que não veio dela.
const SEM_LEITURA = {
  usesEstimate: false, estimate: null, choices: [], proposal: null, matchConfidence: null, originLabel: null, notices: [],
};

function formulario(extra: Partial<MealEditorForm> = {}): MealEditorForm {
  return { local_date: '2026-10-17', meal_slot: 'lunch', eaten_at: '', notes: '', items: [], ai: null, ...extra };
}

describe('nutrition-meal-form: só transporte, o FE nunca manda macro', () => {
  it('item novo nasce na base do alimento e sem quantidade', () => {
    const item = editorItemFromFood(ARROZ, 'k1');
    expect(item).toEqual({
      key: 'k1', id: null, food_id: 3, label: ARROZ.name, basis: 'g', quantity: '', unit: 'g', portion_id: null, ...SEM_LEITURA,
    });
  });

  it('sem quantidade não há corpo; com quantidade, o corpo leva só os campos do contrato', () => {
    const item = editorItemFromFood(ARROZ, 'k1');
    expect(itemsPayload([item])).toBeNull();
    expect(mealPayload(formulario({ items: [{ ...item, quantity: 150 }] }))).toEqual({
      local_date: '2026-10-17',
      meal_slot: 'lunch',
      eaten_at: null,
      notes: null,
      items: [{ food_id: 3, quantity: 150, unit: 'g', portion_id: null }],
    });
    expect(mealPayload(formulario({ meal_slot: null, items: [{ ...item, quantity: 1 }] }))).toBeNull();
  });

  it('porção só vai com unidade de porção (vocabulário do motor) e o item gravado leva o id', () => {
    expect(PORTION_UNITS.length).toBeGreaterThan(0);
    for (const unidade of PORTION_UNITS) expect(unitUsesPortion(unidade)).toBe(true);
    expect(unitUsesPortion('g')).toBe(false);
    const comPorcao = { ...editorItemFromFood(ARROZ, 'k'), id: 9, quantity: '2', unit: PORTION_UNITS[0], portion_id: 7 };
    const emGramas = { ...comPorcao, id: null, unit: 'g', quantity: 30 };
    expect(itemsPayload([comPorcao, emGramas])).toEqual([
      { id: 9, food_id: 3, quantity: 2, unit: PORTION_UNITS[0], portion_id: 7 },
      { food_id: 3, quantity: 30, unit: 'g', portion_id: null },
    ]);
  });

  it('user_id só quando admin/PT registra para um aluno; a refeição gravada volta ao formulário', () => {
    const form = formulario({ eaten_at: '12:30', notes: '  ok ', items: [{ ...editorItemFromFood(ARROZ, 'k'), quantity: 100 }] });
    expect(mealCreatePayload(form, null)).not.toHaveProperty('user_id');
    expect(mealCreatePayload(form, 2)).toMatchObject({ user_id: 2, eaten_at: '12:30', notes: 'ok' });
    const refeicao = {
      id: 1, user_id: 2, local_date: '2026-10-17', meal_slot: 'dinner', eaten_at: null, source: 'manual', notes: null,
      totals: { nutrients: {}, incomplete_fields: [], complete: true, nature: 'DERIVED', item_count: 1, limitations: [] },
      items: [{ id: 5, position: 0, food_id: 3, portion_id: null, portion_label: null, label: ARROZ.name, quantity: 150,
        unit: 'g', amount: 150, basis: 'g', nutrients: {}, nature: 'DERIVED', limitations: [], food_source: 'taco', citation: null,
        field_sources: [] }],
      engine_version: 'nutrition-v1', created_at: null, updated_at: null,
    } satisfies Meal;
    const volta = editorFormFromMeal(refeicao);
    expect(volta.meal_slot).toBe('dinner');
    expect(volta.items).toEqual([
      { key: 'item-5', id: 5, food_id: 3, label: ARROZ.name, basis: 'g', quantity: 150, unit: 'g', portion_id: null, ...SEM_LEITURA },
    ]);
    expect(volta.ai).toBeNull();
  });
});
