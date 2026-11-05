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
  food: Pick<FoodItem, 'id' | 'name' | 'basis' | 'default_portion'>,
  key: string,
  limit: number,
): MealEditorForm {
  if (form.items.length >= limit) return form;
  return { ...form, items: [...form.items, editorItemFromFood(food, key)] };
}

// ---------------------------------------------------------------------------
// Etapa final do cadastro: favoritar e/ou tornar público
// ---------------------------------------------------------------------------

/** O que a pessoa marcou na etapa final. */
export type FinishChoices = { favorite: boolean; promote: boolean };

/** O que já deu certo (não se refaz ao tentar de novo). */
export type FinishDone = { favorite: boolean; promote: boolean };

export type FinishActions = {
  addFavorite: (foodId: number) => Promise<unknown>;
  promote: (foodId: number) => Promise<unknown>;
};

/** A opção "cadastro público" só aparece quando a API diz que o usuário pode promover (D-G: só admin). */
export function canOfferPublic(food: Pick<FoodItem, 'permissions'>): boolean {
  return food.permissions.can_promote === true;
}

/**
 * Executa as ações marcadas, uma de cada vez, pulando as que já deram certo. A primeira que falhar para
 * o fluxo e devolve o erro; o que deu certo fica em `done` para a próxima tentativa não repetir.
 * O alimento já foi salvo: falha aqui nunca o desfaz.
 */
export async function runFinishActions(
  foodId: number,
  choices: FinishChoices,
  done: FinishDone,
  actions: FinishActions,
): Promise<{ done: FinishDone; error: unknown }> {
  const atual = { ...done };
  try {
    if (choices.favorite && !atual.favorite) {
      await actions.addFavorite(foodId);
      atual.favorite = true;
    }
    if (choices.promote && !atual.promote) {
      await actions.promote(foodId);
      atual.promote = true;
    }
  } catch (error) {
    return { done: atual, error: error ?? new Error('falhou') };
  }
  return { done: atual, error: null };
}
