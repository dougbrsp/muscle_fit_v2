import { describe, expect, it } from 'vitest';

import motorConstants from './motor-constants.generated.json';
import { emptyFoodForm, foodFormFromItem, foodFormFromPrefill, foodPayloadFromForm } from './nutrition-food-form';
import type { FoodItem, LabelPhotoReading } from './nutrition-types';

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
  table_base_amount: 30,
  // Valores na base digitada: pronto da API (o front nao converte), diferentes do por 100 de proposito.
  per_table_base: Object.fromEntries(N.food_per_100_fields.map((codigo, i) => [codigo, i === 1 ? null : i + 0.25])),
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

  it('base da tabela: o padrao e o do dono (JSON gerado), nao um numero escrito na tela', () => {
    expect(emptyFoodForm().tableBaseAmount).toBe(N.table_base.default);
    expect(foodPayloadFromForm(emptyFoodForm()).table_base_amount).toBe(N.table_base.default);
  });

  it('envia table_base_amount com os valores como digitados (30 g com 3,3 g de proteina), sem converter', () => {
    const form = { ...emptyFoodForm(), name: 'Iogurte', tableBaseAmount: 30 };
    form.values = { ...form.values, [N.food_per_100_fields[0]]: 66 };
    const payload = foodPayloadFromForm(form);
    expect(payload.table_base_amount).toBe(30);
    expect(payload[N.food_per_100_fields[0]]).toBe(66);
  });

  it('base vazia vai null (a API recusa; a tela nao inventa 100)', () => {
    expect(foodPayloadFromForm({ ...emptyFoodForm(), tableBaseAmount: '' }).table_base_amount).toBeNull();
  });

  it('a edicao reabre com table_base_amount e per_table_base, nao com o por 100', () => {
    const form = foodFormFromItem(ITEM);
    expect(form.tableBaseAmount).toBe(30);
    for (const codigo of N.food_per_100_fields) {
      expect(form.values[codigo]).toBe(ITEM.per_table_base[codigo] ?? '');
      if (ITEM.per_100[codigo] !== null) expect(form.values[codigo]).not.toBe(ITEM.per_100[codigo]);
    }
    const payload = foodPayloadFromForm(form);
    expect(payload.table_base_amount).toBe(30);
    for (const codigo of N.food_per_100_fields) expect(payload[codigo]).toBe(ITEM.per_table_base[codigo]);
  });

  it('vazio vira null (nunca 0) e o payload tem exatamente as chaves do FoodItemIn', () => {
    const payload = foodPayloadFromForm({ ...emptyFoodForm(), name: '  Pão  ' });
    expect(payload.name).toBe('Pão');
    for (const codigo of N.food_per_100_fields) expect(payload[codigo]).toBeNull();
    expect(Object.keys(payload).sort()).toEqual(
      ['name', 'brand', 'category', 'basis', 'table_base_amount', ...N.food_per_100_fields, 'serving_size', 'household_measure', 'servings_per_package'].sort(),
    );
  });

  it('ida e volta preserva os valores do item', () => {
    const payload = foodPayloadFromForm(foodFormFromItem(ITEM));
    expect(payload.brand).toBe('Marca');
    expect(payload.category).toBeNull();
    expect(payload.serving_size).toBe(50);
    expect(payload.household_measure).toBe('1 colher');
    for (const codigo of N.food_per_100_fields) expect(payload[codigo]).toBe(ITEM.per_table_base[codigo]);
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

describe('nutrition-food-form: rotulo por porcao pre-preenche a base = porcao (L6 + base da tabela)', () => {
  const CODIGOS = N.food_per_100_fields;
  const POR_100: Record<string, number | null> = Object.fromEntries(CODIGOS.map((codigo, i) => [codigo, i + 10]));
  const POR_PORCAO: Record<string, number | null> = Object.fromEntries(CODIGOS.map((codigo, i) => [codigo, i + 1]));
  const FOOD = { name: 'Barra', basis: 'g', ...POR_100, serving_size: 30, household_measure: null, servings_per_package: null };

  function leitura(over: Partial<LabelPhotoReading>): LabelPhotoReading {
    return {
      prompt_version: 'v',
      model: 'm',
      legibility: 'good',
      readable: true,
      barcode: null,
      food: FOOD,
      per_serving: POR_PORCAO,
      errors: [],
      warnings: [],
      ai_notes: [],
      ...over,
    };
  }
  const DERIVADO = [{ code: 'label_derived_from_serving:energy_kcal', label: 'x' }];

  it('derivado da porcao: base = porcao e valores por porcao, como lidos', () => {
    const form = foodFormFromPrefill(FOOD, leitura({ warnings: DERIVADO }));
    expect(form.tableBaseAmount).toBe(30);
    for (const codigo of CODIGOS) expect(form.values[codigo]).toBe(POR_PORCAO[codigo]);
    expect(foodPayloadFromForm(form).table_base_amount).toBe(30);
  });

  it('sem sinal de derivacao, mantem por 100 e a base padrao', () => {
    const form = foodFormFromPrefill(FOOD, leitura({}));
    expect(form.tableBaseAmount).toBe(N.table_base.default);
    for (const codigo of CODIGOS) expect(form.values[codigo]).toBe(POR_100[codigo]);
  });

  it('sem porcao positiva ou perdendo campo por 100, mantem por 100', () => {
    expect(foodFormFromPrefill({ ...FOOD, serving_size: null }, leitura({ warnings: DERIVADO })).tableBaseAmount).toBe(
      N.table_base.default,
    );
    const semUm = { ...POR_PORCAO, [CODIGOS[0]]: null };
    expect(foodFormFromPrefill(FOOD, leitura({ warnings: DERIVADO, per_serving: semUm })).tableBaseAmount).toBe(
      N.table_base.default,
    );
  });

  it('sem a leitura (chamada antiga), segue por 100', () => {
    expect(foodFormFromPrefill(FOOD).tableBaseAmount).toBe(N.table_base.default);
  });
});
