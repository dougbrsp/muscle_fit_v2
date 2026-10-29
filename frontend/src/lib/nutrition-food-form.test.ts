import { describe, expect, it } from 'vitest';

import motorConstants from './motor-constants.generated.json';
import { emptyFoodForm, foodFormFromItem, foodFormFromPrefill, foodPayloadFromForm } from './nutrition-food-form';
import type { FoodItem } from './nutrition-types';

const N = motorConstants.nutrition;

const ITEM: FoodItem = {
  id: 7,
  source: 'user',
  source_id: null,
  source_version: null,
  barcode: null,
  name: 'Arroz caseiro',
  brand: 'Marca',
  category: null,
  basis: 'g',
  per_100: Object.fromEntries(N.food_per_100_fields.map((codigo, i) => [codigo, i === 1 ? null : i + 0.5])),
  serving: { serving_size: 50, household_measure: '1 colher', servings_per_package: null },
  license: null,
  citation: null,
  field_sources: [],
  is_public: false,
  is_own: true,
  is_active: true,
  permissions: { can_edit: true, can_add_portion: true, can_promote: false },
  created_at: null,
  updated_at: null,
};

describe('nutrition-food-form: formulário <-> FoodItemIn (só transporte)', () => {
  it('formulário vazio tem todos os campos por 100 do dono e a base padrão', () => {
    const vazio = emptyFoodForm();
    expect(Object.keys(vazio.values)).toEqual(N.food_per_100_fields);
    expect(vazio.basis).toBe(N.default_food_basis);
  });

  it('vazio vira null (nunca 0) e o payload tem exatamente as chaves do FoodItemIn', () => {
    const payload = foodPayloadFromForm({ ...emptyFoodForm(), name: '  Pão  ' });
    expect(payload.name).toBe('Pão');
    for (const codigo of N.food_per_100_fields) expect(payload[codigo]).toBeNull();
    expect(Object.keys(payload).sort()).toEqual(
      ['name', 'brand', 'category', 'basis', ...N.food_per_100_fields, 'serving_size', 'household_measure', 'servings_per_package'].sort(),
    );
  });

  it('ida e volta preserva os valores do item', () => {
    const payload = foodPayloadFromForm(foodFormFromItem(ITEM));
    expect(payload.brand).toBe('Marca');
    expect(payload.category).toBeNull();
    expect(payload.serving_size).toBe(50);
    expect(payload.household_measure).toBe('1 colher');
    for (const codigo of N.food_per_100_fields) expect(payload[codigo]).toBe(ITEM.per_100[codigo]);
  });
});

describe('nutrition-food-form: leitura da foto do rótulo (L6) pré-preenche o formulário', () => {
  const LIDO = {
    name: 'Biscoito de aveia',
    brand: null,
    category: null,
    basis: 'g',
    ...Object.fromEntries(N.food_per_100_fields.map((codigo, i) => [codigo, i === 2 ? null : i + 1.25])),
    serving_size: 30,
    household_measure: '3 unidades',
    servings_per_package: null,
  };

  it('valores lidos entram; não lidos ficam vazios (nunca 0) e voltam null no payload', () => {
    const form = foodFormFromPrefill(LIDO);
    expect(form.name).toBe('Biscoito de aveia');
    expect(form.brand).toBe('');
    expect(form.servingSize).toBe(30);
    expect(form.servingsPerPackage).toBe('');
    const payload = foodPayloadFromForm(form);
    for (const codigo of N.food_per_100_fields) expect(payload[codigo]).toBe(LIDO[codigo as keyof typeof LIDO]);
    expect(payload.servings_per_package).toBeNull();
  });

  it('sem base lida, a base padrão do dono', () => {
    expect(foodFormFromPrefill({ ...LIDO, basis: null }).basis).toBe(N.default_food_basis);
  });
});
