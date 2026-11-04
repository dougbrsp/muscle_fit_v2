// Apresentação da lista de refeições do diário (nutricao-melhorias, Frente C): as duas
// linhas de cada refeição e o tom do selo de natureza. Nada é calculado aqui: os totais
// vêm de `meal.totals.nutrients` (motor `meal_math`) e a ordem da lista é da API
// (`diary.meal_sort_key`).

import { DASH, formatNumberOrDash } from './format';
import { CATALOG_ITEM_NATURE, ESTIMATE_ITEM_NATURE, MEAL_SLOT_OPTIONS, nutritionLabel, type NutritionCode } from './nutrition-constants';
import { QUICK_ENTRY_NATURE, QUICK_ENTRY_NUTRIENT_FIELDS } from './nutrition-quick-entry';
import type { Meal, MealItem, NutritionTotals } from './nutrition-types';

/** Natureza das refeições que misturam origens (código do motor, `MealNature.MIXED`). */
const MIXED_NATURE: NutritionCode = 'MIXED';

/** Hora e nome da refeição: `12:30 - Almoço (3 itens)`; 1 item = nome do item; sem hora = só o nome. */
export function mealHeadline(meal: Pick<Meal, 'meal_slot' | 'eaten_at' | 'items'>): string {
  const slot = nutritionLabel(MEAL_SLOT_OPTIONS, meal.meal_slot) ?? meal.meal_slot;
  const nome = meal.items.length === 1 ? meal.items[0].label : `${slot} (${meal.items.length} itens)`;
  return meal.eaten_at ? `${meal.eaten_at} - ${nome}` : nome;
}

// Rótulo de cada valor da segunda linha, por campo do dono (energia abre a linha).
const MACRO_LINE_LABELS: Record<string, string> = {
  energy_kcal: 'Macros',
  protein_g: 'Proteína',
  carbohydrates_g: 'Carbo',
  total_fat_g: 'Gordura',
};
const MACRO_LINE_UNITS: Record<string, string> = { energy_kcal: 'kcal', protein_g: 'g', carbohydrates_g: 'g', total_fat_g: 'g' };

function macroPart(totals: NutritionTotals, code: string): string {
  const unit = MACRO_LINE_UNITS[code] ?? '';
  const valor = totals.nutrients[code];
  const texto = formatNumberOrDash(valor, code === 'energy_kcal' ? 0 : 1);
  // Ausente = `-` (nunca 0 falso); campo incompleto é um mínimo e vem com `≥`.
  if (texto === DASH) return DASH;
  return `${totals.incomplete_fields.includes(code) ? '≥' : ''}${texto}${unit}`;
}

/** `Macros: 520kcal Proteína: 32g Carbo: 60g Gordura: 14g`, direto de `totals.nutrients`. */
export function mealMacrosLine(totals: NutritionTotals): string {
  return QUICK_ENTRY_NUTRIENT_FIELDS.map((code) => `${MACRO_LINE_LABELS[code] ?? code}: ${macroPart(totals, code)}`).join(' ');
}

export type NatureTone = 'brand' | 'gray' | 'yellow';

/**
 * Tom do selo de natureza. Amarelo = há valor estimado (item `ESTIMATED`); catálogo = marca;
 * informado pela pessoa (lançamento rápido) e mistura sem estimativa = neutro.
 */
export function natureTone(nature: NutritionCode, items: ReadonlyArray<Pick<MealItem, 'nature'>>): NatureTone {
  if (nature === CATALOG_ITEM_NATURE) return 'brand';
  if (nature === ESTIMATE_ITEM_NATURE) return 'yellow';
  if (nature === MIXED_NATURE) return items.some((item) => item.nature === ESTIMATE_ITEM_NATURE) ? 'yellow' : 'gray';
  return 'gray';
}

/** O item é um lançamento rápido (selo neutro com o rótulo da natureza). */
export function isQuickEntryItem(item: Pick<MealItem, 'nature'>): boolean {
  return item.nature === QUICK_ENTRY_NATURE;
}
