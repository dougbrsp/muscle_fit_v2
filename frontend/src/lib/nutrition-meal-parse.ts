// Leitura da refeição por IA (Lote L5b) -> estado do editor de refeição. Só
// transporte: a decisão de cada item (alimento pré-selecionado, candidatos,
// estimativa), a quantidade proposta e os nutrientes são do motor
// (`domain/nutrition/meal_parse`, `meal_math`), que a API aplica na leitura, no
// preview e na gravação. A tela não decide nada pela nota nem por número.

import { AI_ESTIMATE_BASIS, MEAL_PARSE_STATUS_OPTIONS, nutritionLabel } from './nutrition-constants';
import type { MealEditorForm, MealEditorItem } from './nutrition-meal-form';
import type { MealParseItem, MealParseResult } from './nutrition-types';

/** Um item lido -> item do editor, com a proposta do motor. Sem alimento
 * pré-selecionado e sem estimativa como proposta, o item fica sem origem até a
 * pessoa escolher (o editor não deixa salvar). */
export function editorItemFromParse(item: MealParseItem, key: string): MealEditorItem {
  const escolhido = item.food_id != null ? item.candidates.find((c) => c.food_id === item.food_id) : undefined;
  const nome = escolhido?.name ?? (item.uses_estimate && item.estimate ? item.estimate.name : item.ai.name);
  return {
    key,
    id: null,
    food_id: escolhido ? escolhido.food_id : null,
    label: nome,
    basis: escolhido?.basis ?? AI_ESTIMATE_BASIS,
    quantity: item.quantity ?? '',
    unit: item.unit,
    portion_id: null,
    usesEstimate: item.uses_estimate,
    estimate: item.estimate,
    choices: item.candidates,
    proposal: { quantity: item.quantity ?? '', unit: item.unit },
    matchConfidence: escolhido ? item.match_confidence : null,
    originLabel: nutritionLabel(MEAL_PARSE_STATUS_OPTIONS, item.status),
    notices: item.limitations,
  };
}

/** Resultado da leitura -> formulário do editor (dia e refeição da leitura, ou os da tela). */
export function editorFormFromParse(
  result: MealParseResult,
  context: { date: string; slot: string | null; text: string },
): MealEditorForm {
  return {
    local_date: result.local_date ?? context.date,
    meal_slot: result.meal_slot ?? context.slot,
    eaten_at: '',
    notes: '',
    items: result.items.map((item) => editorItemFromParse(item, `ia-${item.index}`)),
    ai: result.job_id
      ? { job_id: result.job_id, description_raw: context.text, notes: result.notes, limitations: result.limitations }
      : null,
  };
}
