import { describe, expect, it } from 'vitest';

import { MEAL_ITEMS_LIMIT } from './nutrition-constants';
import {
  addRegisteredFood,
  manualFormWithName,
  registrationAfterBarcodeNotFound,
  registrationName,
} from './nutrition-food-registration';
import { editorItemFromFood, type MealEditorForm } from './nutrition-meal-form';

const RASCUNHO: MealEditorForm = {
  local_date: '2026-11-04',
  meal_slot: 'lunch',
  eaten_at: '12:30',
  notes: 'sem sal',
  items: [{ ...editorItemFromFood({ id: 1, name: 'Arroz', basis: 'g' }, 'novo-1'), quantity: 150 }],
  ai: null,
};

describe('nutrition-food-registration: cadastro dentro do registro da refeicao', () => {
  it('o termo buscado vira o nome (aparado) e o resto do formulario fica vazio', () => {
    expect(registrationName('  iogurte   proteico ')).toBe('iogurte proteico');
    expect(manualFormWithName('  iogurte ').name).toBe('iogurte');
    expect(manualFormWithName(undefined).name).toBe('');
  });

  it('o alimento salvo entra como item e o rascunho fica intacto (refeicao, hora, dia, anotacao, quantidades)', () => {
    const novo = addRegisteredFood(RASCUNHO, { id: 41, name: 'Iogurte proteico', basis: 'g' }, 'novo-2', MEAL_ITEMS_LIMIT);
    expect(novo.items).toHaveLength(2);
    expect(novo.items[1]).toMatchObject({ food_id: 41, label: 'Iogurte proteico', unit: 'g', quantity: '' });
    expect(novo.items[0]).toBe(RASCUNHO.items[0]);
    expect(novo.items[0].quantity).toBe(150);
    expect({ ...novo, items: [] }).toEqual({ ...RASCUNHO, items: [] });
    // Imutavel: o rascunho original (o que vale se o usuario fechar sem salvar) nao foi tocado.
    expect(RASCUNHO.items).toHaveLength(1);
  });

  it('no teto de itens devolve o mesmo rascunho', () => {
    const cheio = {
      ...RASCUNHO,
      items: Array.from({ length: MEAL_ITEMS_LIMIT }, (_, i) =>
        editorItemFromFood({ id: i + 1, name: 'A', basis: 'g' }, `k-${i}`),
      ),
    };
    expect(addRegisteredFood(cheio, { id: 99, name: 'B', basis: 'g' }, 'k-x', MEAL_ITEMS_LIMIT)).toBe(cheio);
  });

  it('codigo de barras sem resultado cai na digitacao com o codigo preenchido', () => {
    expect(registrationAfterBarcodeNotFound(' 7891000100103 ')).toEqual({ tab: 'manual', barcode: '7891000100103' });
    expect(registrationAfterBarcodeNotFound('   ')).toBeNull();
  });
});
