// Alimentos preferidos (Lote N3) <-> telas. Só transporte e ordem de exibição.
//
// Teto da lista, validação da porção padrão (conversão do diário, `meal_math`) e
// ordem são do motor (`domain/nutrition/favorites`), que a API aplica e responde
// 400/409 com os códigos. O favorito vira item do editor com a quantidade, a
// unidade e a porção que o usuário escolheu; os nutrientes saem do preview da
// API (`meal_math`, decisão E9): a tela NÃO calcula macro nem converte porção.

import { UNIT_OPTIONS, nutritionLabel, type NutritionCode } from './nutrition-constants';
import { inputNumberOrNull } from './nutrition-format';
import { editorItemFromFood, type MealEditorItem } from './nutrition-meal-form';
import type { FavoriteDefaultPayload, FavoriteItem } from './nutrition-types';

/** Ids dos alimentos favoritos (para marcar a estrela na busca). */
export function favoriteIds(items: readonly FavoriteItem[]): Set<number> {
  return new Set(items.map((item) => item.food.id));
}

/** Favoritos primeiro, mantendo a ordem relativa de cada grupo (a ordem da busca é do motor). */
export function favoritesFirst<T extends { id: number }>(items: readonly T[], ids: ReadonlySet<number>): T[] {
  return [...items.filter((item) => ids.has(item.id)), ...items.filter((item) => !ids.has(item.id))];
}

/** Move o alimento `delta` posições (-1 sobe, +1 desce); fora dos limites devolve a lista como está. */
export function moveFavorite(foodIds: readonly number[], foodId: number, delta: -1 | 1): number[] {
  const de = foodIds.indexOf(foodId);
  const para = de + delta;
  if (de < 0 || para < 0 || para >= foodIds.length) return [...foodIds];
  const nova = [...foodIds];
  [nova[de], nova[para]] = [nova[para], nova[de]];
  return nova;
}

/** Item novo do editor de refeição a partir do favorito, já com a porção padrão (se houver). */
export function editorItemFromFavorite(favorite: FavoriteItem, key: string): MealEditorItem {
  const item = editorItemFromFood(favorite.food, key);
  const padrao = favorite.default;
  if (!padrao) return item;
  return { ...item, quantity: padrao.quantity, unit: padrao.unit, portion_id: padrao.portion_id };
}

export type FavoriteDefaultForm = {
  /** `''` enquanto vazio. */
  quantity: number | string;
  unit: NutritionCode | null;
  portionId: number | null;
};

export function favoriteDefaultFormFrom(favorite: FavoriteItem): FavoriteDefaultForm {
  const padrao = favorite.default;
  return padrao
    ? { quantity: padrao.quantity, unit: padrao.unit, portionId: padrao.portion_id }
    : { quantity: '', unit: null, portionId: null };
}

/** Corpo do PUT: quantidade vazia limpa a porção padrão (tudo `null`); a API valida o resto. */
export function favoriteDefaultPayload(form: FavoriteDefaultForm): FavoriteDefaultPayload {
  const quantidade = inputNumberOrNull(form.quantity);
  if (quantidade === null && form.unit === null) {
    return { default_quantity: null, default_unit: null, default_portion_id: null };
  }
  return { default_quantity: quantidade, default_unit: form.unit, default_portion_id: form.portionId };
}

/** Texto curto da porção padrão ("2 × Colher de servir", "150 g"); `null` sem porção padrão. */
export function favoriteDefaultSummary(favorite: FavoriteItem): string | null {
  const padrao = favorite.default;
  if (!padrao) return null;
  if (padrao.portion_label) return `${padrao.quantity} × ${padrao.portion_label}`;
  return `${padrao.quantity} ${nutritionLabel(UNIT_OPTIONS, padrao.unit) ?? padrao.unit}`;
}
