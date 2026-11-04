// Cadastro de alimento aberto de dentro do registro da refeição (Frente B, nutricao-melhorias).
// Só transporte e estado de tela: o que vale como alimento, o EAN, a base da tabela e os avisos são
// da API (motor `domain/nutrition`). Nada de regra numérica aqui.

import { emptyFoodForm, type FoodFormState } from './nutrition-food-form';
import { editorItemFromFood, type MealEditorForm } from './nutrition-meal-form';
import type { FoodItem } from './nutrition-types';

/** Abas do fluxo de cadastro. `usda` só existe na página de alimentos. */
export type RegistrationTab = 'manual' | 'barcode' | 'label' | 'usda';

/** Nome pré-preenchido a partir do termo buscado (aparado; a API valida o tamanho). */
export function registrationName(term: string | null | undefined): string {
  return (term ?? '').trim().replace(/\s+/g, ' ');
}

/** Formulário de digitação já com o termo buscado no nome. */
export function manualFormWithName(term: string | null | undefined): FoodFormState {
  return { ...emptyFoodForm(), name: registrationName(term) };
}

/** Código lido/digitado sem produto: o cadastro cai na digitação (aba) com o código preenchido. */
export function registrationAfterBarcodeNotFound(ean: string): { tab: RegistrationTab; barcode: string } | null {
  const codigo = ean.trim();
  return codigo ? { tab: 'manual', barcode: codigo } : null;
}

/**
 * Alimento cadastrado (ou já existente) entra como item da refeição; o resto do rascunho (refeição, hora, dia,
 * itens, quantidades, anotação) fica como está. No teto de itens devolve o mesmo rascunho.
 */
export function addRegisteredFood(
  form: MealEditorForm,
  food: Pick<FoodItem, 'id' | 'name' | 'basis'>,
  key: string,
  limit: number,
): MealEditorForm {
  if (form.items.length >= limit) return form;
  return { ...form, items: [...form.items, editorItemFromFood(food, key)] };
}
