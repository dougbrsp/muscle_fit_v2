// Formulário de alimento do catálogo (Lote L3a) <-> corpo de
// `POST/PUT /nutrition/foods` (`FoodItemIn`, `extra="forbid"`).
//
// Só transporte. Quais campos existem, com que rótulo e unidade, e quais são
// obrigatórios vêm do motor (`FOOD_PER_100_FIELDS`, `FOOD_SERVING_FIELDS`,
// `FOOD_REQUIRED_FIELDS` de `nutrition-constants.ts`, JSON gerado). Faixas,
// base e consistência (Atwater) são da API, que responde 400 com o código do
// motor ou devolve `warnings`. Campo vazio vai `null`, nunca 0.

import { DEFAULT_FOOD_BASIS, FOOD_PER_100_FIELDS, type NutritionCode } from './nutrition-constants';
import { inputNumberOrNull } from './nutrition-format';
import type { FoodItem, FoodItemPayload } from './nutrition-types';

export type FoodFormNumber = number | string;

export type FoodFormState = {
  name: string;
  brand: string;
  category: string;
  basis: NutritionCode;
  /** Valores por 100 g/ml, chave = código da RDC 429 (`FOOD_PER_100_FIELDS`). */
  values: Record<string, FoodFormNumber>;
  servingSize: FoodFormNumber;
  householdMeasure: string;
  servingsPerPackage: FoodFormNumber;
};

function asField(value: number | null | undefined): FoodFormNumber {
  return value == null ? '' : value;
}

function textOrNull(value: string): string | null {
  const texto = value.trim().replace(/\s+/g, ' ');
  return texto ? texto : null;
}

export function emptyFoodForm(): FoodFormState {
  return {
    name: '',
    brand: '',
    category: '',
    basis: DEFAULT_FOOD_BASIS,
    values: Object.fromEntries(FOOD_PER_100_FIELDS.map((campo) => [campo.code, ''])),
    servingSize: '',
    householdMeasure: '',
    servingsPerPackage: '',
  };
}

export function foodFormFromItem(item: FoodItem): FoodFormState {
  return {
    name: item.name,
    brand: item.brand ?? '',
    category: item.category ?? '',
    basis: item.basis,
    values: Object.fromEntries(FOOD_PER_100_FIELDS.map((campo) => [campo.code, asField(item.per_100[campo.code])])),
    servingSize: asField(item.serving.serving_size),
    householdMeasure: item.serving.household_measure ?? '',
    servingsPerPackage: asField(item.serving.servings_per_package),
  };
}

function prefillNumber(value: string | number | null | undefined): FoodFormNumber {
  return typeof value === 'number' ? value : '';
}

function prefillText(value: string | number | null | undefined): string {
  return typeof value === 'string' ? value : '';
}

/**
 * Formulário pré-preenchido pela leitura da foto do rótulo (Lote L6): os campos
 * do cadastro que a API devolveu (`LabelPhotoReading.food`, já revisados pelo
 * motor). Campo não lido fica vazio, nunca 0; a base desconhecida fica a padrão.
 */
export function foodFormFromPrefill(food: FoodItemPayload): FoodFormState {
  const base = prefillText(food.basis);
  return {
    name: prefillText(food.name),
    brand: prefillText(food.brand),
    category: prefillText(food.category),
    basis: base || DEFAULT_FOOD_BASIS,
    values: Object.fromEntries(FOOD_PER_100_FIELDS.map((campo) => [campo.code, prefillNumber(food[campo.code])])),
    servingSize: prefillNumber(food.serving_size),
    householdMeasure: prefillText(food.household_measure),
    servingsPerPackage: prefillNumber(food.servings_per_package),
  };
}

export function foodPayloadFromForm(form: FoodFormState): FoodItemPayload {
  const porCem = Object.fromEntries(
    FOOD_PER_100_FIELDS.map((campo) => [campo.code, inputNumberOrNull(form.values[campo.code])]),
  );
  return {
    name: form.name.trim(),
    brand: textOrNull(form.brand),
    category: textOrNull(form.category),
    basis: form.basis,
    ...porCem,
    serving_size: inputNumberOrNull(form.servingSize),
    household_measure: textOrNull(form.householdMeasure),
    servings_per_package: inputNumberOrNull(form.servingsPerPackage),
  };
}
