// Estado do editor de refeição do diário (Lote L4) e o corpo de `POST/PUT
// /nutrition/meals` e do preview. Só transporte: conversão de unidade,
// porção, macros e totais são do motor (`domain/nutrition/meal_math`), que a
// API aplica no preview e na gravação (decisão E9: o FE não calcula). O FE
// nunca calcula macro; o item já gravado vai com `id` para a API reusar os
// valores congelados (E4).
//
// Leitura por IA (L5b): o item pode vir de uma leitura (`nutrition-meal-parse.ts`)
// com o alimento pré-selecionado, os candidatos para a pessoa escolher ou a
// estimativa da IA (fora do catálogo). A estimativa vai para a API como a
// leitura entregou; os nutrientes do item estimado também são do motor.

import {
  AI_ESTIMATE_BASIS,
  MEAL_NATURE_OPTIONS,
  PORTION_UNITS,
  nutritionLabel,
  type NutritionCode,
} from './nutrition-constants';
import type {
  FoodItem,
  Meal,
  MealCreatePayload,
  MealItemEstimatePayload,
  MealItemPayload,
  MealParseCandidate,
  MealPayload,
  NutritionLimitation,
} from './nutrition-types';

export type MealEditorItem = {
  /** Chave estável na tela (o `id` do item gravado ou uma nova). */
  key: string;
  /** Item já gravado da refeição (edição) ou `null` (novo). */
  id: number | null;
  /** Alimento do catálogo; `null` no item estimado pela IA ou ainda sem escolha. */
  food_id: number | null;
  label: string;
  /** Base da tabela do alimento (g | ml): unidade inicial do item novo. */
  basis: NutritionCode;
  /** `''` enquanto o campo está vazio. */
  quantity: number | string;
  unit: NutritionCode;
  portion_id: number | null;
  /** Origem = estimativa da IA (nova, em `estimate`, ou a gravada, reusada pelo `id`). */
  usesEstimate: boolean;
  /** Estimativa da IA por 100 g: a proposta do item estimado e a alternativa da escolha. */
  estimate: MealItemEstimatePayload | null;
  /** Candidatos do catálogo da leitura por IA (a pessoa pode trocar o alimento). */
  choices: MealParseCandidate[];
  /** Quantidade e unidade que o motor propôs na leitura (voltar para a estimativa as restaura). */
  proposal: { quantity: number | string; unit: NutritionCode } | null;
  matchConfidence: number | null;
  /** Rótulo pronto do motor para a origem do item (leitura por IA ou estimado gravado). */
  originLabel: string | null;
  /** Avisos da leitura (quantidade estimada, alimento fora do catálogo...). */
  notices: NutritionLimitation[];
};

export type MealEditorForm = {
  local_date: string;
  meal_slot: NutritionCode | null;
  /** `HH:MM` ou `''`. */
  eaten_at: string;
  notes: string;
  items: MealEditorItem[];
  /** Leitura por IA que originou a refeição (L5b): o job e o texto descrito. */
  ai: { job_id: string; description_raw: string; notes: string | null; limitations: NutritionLimitation[] } | null;
};

const SEM_LEITURA: Pick<
  MealEditorItem,
  'usesEstimate' | 'estimate' | 'choices' | 'proposal' | 'matchConfidence' | 'originLabel' | 'notices'
> = {
  usesEstimate: false,
  estimate: null,
  choices: [],
  proposal: null,
  matchConfidence: null,
  originLabel: null,
  notices: [],
};

export function editorItemFromFood(food: Pick<FoodItem, 'id' | 'name' | 'basis'>, key: string): MealEditorItem {
  return {
    key,
    id: null,
    food_id: food.id,
    label: food.name,
    basis: food.basis,
    quantity: '',
    unit: food.basis,
    portion_id: null,
    ...SEM_LEITURA,
  };
}

/** Editor a partir da refeição gravada. O item estimado pela IA (sem `food_id`)
 * fica com a origem `estimativa` e é reenviado só com o `id` (a API reusa o snapshot). */
export function editorFormFromMeal(meal: Meal): MealEditorForm {
  return {
    local_date: meal.local_date,
    meal_slot: meal.meal_slot,
    eaten_at: meal.eaten_at ?? '',
    notes: meal.notes ?? '',
    items: meal.items.map((item) => ({
      key: `item-${item.id ?? item.position}`,
      id: item.id,
      food_id: item.food_id,
      label: item.label,
      basis: item.basis,
      quantity: item.quantity,
      unit: item.unit,
      portion_id: item.portion_id,
      ...SEM_LEITURA,
      usesEstimate: item.food_id == null,
      originLabel: item.food_id == null ? nutritionLabel(MEAL_NATURE_OPTIONS, item.nature) : null,
    })),
    ai: null,
  };
}

/** A unidade usa uma porção (cadastrada ou do rótulo)? Vocabulário do motor (`PORTION_UNITS`). */
export function unitUsesPortion(unit: NutritionCode): boolean {
  return PORTION_UNITS.includes(unit);
}

/** O item tem origem definida (alimento do catálogo ou estimativa da IA)? */
export function itemHasSource(item: MealEditorItem): boolean {
  if (item.food_id != null) return true;
  return item.usesEstimate && (item.estimate !== null || item.id !== null);
}

/** Troca a origem do item por um candidato do catálogo da leitura. */
export function chooseCandidate(item: MealEditorItem, candidate: MealParseCandidate): MealEditorItem {
  return {
    ...item,
    food_id: candidate.food_id,
    label: candidate.name,
    basis: candidate.basis ?? item.basis,
    usesEstimate: false,
    portion_id: null,
    matchConfidence: candidate.score,
  };
}

/** Troca a origem do item pela estimativa da IA (alimento fora do catálogo): a
 * base é a da estimativa e a quantidade volta a ser a que o motor propôs. */
export function chooseEstimate(item: MealEditorItem): MealEditorItem {
  if (!item.estimate) return item;
  return {
    ...item,
    food_id: null,
    label: item.estimate.name,
    basis: AI_ESTIMATE_BASIS,
    usesEstimate: true,
    portion_id: null,
    ...(item.proposal ? { quantity: item.proposal.quantity, unit: item.proposal.unit } : {}),
    matchConfidence: null,
  };
}

function quantidade(valor: number | string): number | null {
  if (valor === '' || valor === null) return null;
  const numero = Number(valor);
  return Number.isFinite(numero) ? numero : null;
}

/** Itens no formato da API; `null` enquanto algum item está sem quantidade ou sem origem (o motor confere o resto). */
export function itemsPayload(items: MealEditorItem[]): MealItemPayload[] | null {
  const corpo: MealItemPayload[] = [];
  for (const item of items) {
    const valor = quantidade(item.quantity);
    if (valor === null || !itemHasSource(item)) return null;
    const base = {
      ...(item.id != null ? { id: item.id } : {}),
      quantity: valor,
      unit: item.unit,
      ...(item.matchConfidence != null ? { match_confidence: item.matchConfidence } : {}),
    };
    if (item.food_id != null) {
      corpo.push({ ...base, food_id: item.food_id, portion_id: unitUsesPortion(item.unit) ? item.portion_id : null });
    } else {
      // Estimado: a estimativa nova vai inteira; o gravado mantido vai só com o `id`.
      corpo.push({ ...base, food_id: null, ...(item.estimate ? { estimate: item.estimate } : {}), portion_id: null });
    }
  }
  return corpo;
}

/** Corpo de `PUT /nutrition/meals/{id}`; `null` sem refeição escolhida ou com item sem quantidade/origem. */
export function mealPayload(form: MealEditorForm): MealPayload | null {
  const itens = itemsPayload(form.items);
  if (itens === null || form.meal_slot === null) return null;
  return {
    local_date: form.local_date,
    meal_slot: form.meal_slot,
    eaten_at: form.eaten_at || null,
    notes: form.notes.trim() || null,
    items: itens,
  };
}

/** Corpo de `POST /nutrition/meals` (e do preview): `user_id` só quando admin/PT registra para um aluno;
 * a leitura por IA vai junto (`ai_job_id`, `description_raw`). */
export function mealCreatePayload(form: MealEditorForm, userId: number | null): MealCreatePayload | null {
  const corpo = mealPayload(form);
  if (corpo === null) return null;
  return {
    ...corpo,
    ...(userId != null ? { user_id: userId } : {}),
    ...(form.ai ? { ai_job_id: form.ai.job_id, description_raw: form.ai.description_raw } : {}),
  };
}
