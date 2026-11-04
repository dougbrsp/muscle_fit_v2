// Formulário de alimento do catálogo (Lote L3a) <-> corpo de
// `POST/PUT /nutrition/foods` (`FoodItemIn`, `extra="forbid"`).
//
// Só transporte. Quais campos existem, com que rótulo e unidade, e quais são
// obrigatórios vêm do motor (`FOOD_PER_100_FIELDS`, `FOOD_SERVING_FIELDS`,
// `FOOD_REQUIRED_FIELDS` de `nutrition-constants.ts`, JSON gerado). Faixas,
// base e consistência (Atwater) são da API, que responde 400 com o código do
// motor ou devolve `warnings`. Campo vazio vai `null`, nunca 0.

import { DEFAULT_FOOD_BASIS, FOOD_PER_100_FIELDS, FOOD_TABLE_BASE, type NutritionCode } from './nutrition-constants';
import { inputNumberOrNull } from './nutrition-format';
import type { FoodItem, FoodItemPayload, LabelPhotoReading } from './nutrition-types';

export type FoodFormNumber = number | string;

export type FoodFormState = {
  name: string;
  brand: string;
  category: string;
  basis: NutritionCode;
  /** Valores da tabela NA BASE digitada (`tableBaseAmount`), chave = código da RDC 429 (`FOOD_PER_100_FIELDS`). A conversão para por 100 é da API. */
  values: Record<string, FoodFormNumber>;
  /** Quantidade (g/ml) a que a tabela se refere ("valores por X g"); padrão do dono, validada pela API. */
  tableBaseAmount: FoodFormNumber;
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
    tableBaseAmount: FOOD_TABLE_BASE.default,
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
    // Reabre na base em que foi digitado (`per_table_base`, calculado pela API); alimento sem o campo cai no por 100.
    values: Object.fromEntries(
      FOOD_PER_100_FIELDS.map((campo) => [campo.code, asField((item.per_table_base ?? item.per_100)[campo.code])]),
    ),
    tableBaseAmount: item.table_base_amount ?? FOOD_TABLE_BASE.default,
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
export function foodFormFromPrefill(food: FoodItemPayload, reading?: LabelPhotoReading): FoodFormState {
  const base = prefillText(food.basis);
  // A base e os valores nela vêm prontos da leitura (motor); leitura antiga sem eles = por 100 de `food`.
  const baseLida = reading?.table_base_amount;
  const valoresNaBase = baseLida != null ? reading?.per_table_base : undefined;
  return {
    name: prefillText(food.name),
    brand: prefillText(food.brand),
    category: prefillText(food.category),
    basis: base || DEFAULT_FOOD_BASIS,
    values: Object.fromEntries(
      FOOD_PER_100_FIELDS.map((campo) => [campo.code, prefillNumber(valoresNaBase ? valoresNaBase[campo.code] : food[campo.code])]),
    ),
    tableBaseAmount: valoresNaBase && baseLida != null ? baseLida : FOOD_TABLE_BASE.default,
    servingSize: prefillNumber(food.serving_size),
    householdMeasure: prefillText(food.household_measure),
    servingsPerPackage: prefillNumber(food.servings_per_package),
  };
}

/**
 * Base vazia ou fora da faixa do dono (`nutrition.table_base`): a tela bloqueia salvar e mostra o erro no
 * campo; a API confere de novo (400 `invalid_table_base`). Devolve a mensagem ou `null` se a base serve.
 */
export function tableBaseError(value: FoodFormNumber): string | null {
  const numero = inputNumberOrNull(value);
  if (numero == null) return 'Informe a quantidade a que os valores da tabela se referem.';
  if (numero < FOOD_TABLE_BASE.min || numero > FOOD_TABLE_BASE.max) {
    return `A base precisa estar entre ${FOOD_TABLE_BASE.min} e ${FOOD_TABLE_BASE.max}.`;
  }
  return null;
}

/** Corpo do cadastro. `table_base_amount` nunca é `null`: a tela só chama depois de `tableBaseError` = `null`. */
export function foodPayloadFromForm(form: FoodFormState): FoodItemPayload {
  const base = inputNumberOrNull(form.tableBaseAmount);
  if (base == null) throw new Error(tableBaseError(form.tableBaseAmount) ?? 'Base da tabela vazia.');
  const porCem = Object.fromEntries(
    FOOD_PER_100_FIELDS.map((campo) => [campo.code, inputNumberOrNull(form.values[campo.code])]),
  );
  return {
    name: form.name.trim(),
    brand: textOrNull(form.brand),
    category: textOrNull(form.category),
    basis: form.basis,
    table_base_amount: base,
    ...porCem,
    serving_size: inputNumberOrNull(form.servingSize),
    household_measure: textOrNull(form.householdMeasure),
    servings_per_package: inputNumberOrNull(form.servingsPerPackage),
  };
}
