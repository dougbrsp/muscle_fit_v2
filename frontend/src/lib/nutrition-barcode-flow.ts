// Fluxo do código de barras na tela de alimentos (N3), como funções puras testáveis sem DOM.
// A decisão do que vale (EAN válido, produto utilizável, conflito) é da API; aqui só se traduz a
// resposta em estado de tela.

import { barcodeConflict, nutritionErrorCode } from './nutrition-api';

/** Respostas da consulta que significam "o código é válido, mas não há produto utilizável". */
const NOT_FOUND_CODES = ['off_not_found', 'invalid_external_food', 'food_not_found'];

export function isBarcodeNotFound(error: unknown): boolean {
  return NOT_FOUND_CODES.includes(nutritionErrorCode(error) ?? '');
}

/** Estado inicial do cadastro manual aberto a partir do EAN (lido ou digitado); `null` se vazio. */
export type NewFoodFromBarcode = { mode: 'new'; barcode: string };

export function newFoodFromBarcode(ean: string | null | undefined): NewFoodFromBarcode | null {
  const codigo = (ean ?? '').trim();
  return codigo ? { mode: 'new', barcode: codigo } : null;
}

/** Consulta sem produto: o cadastro abre direto com o código preenchido (sem botão intermediário). */
export function editingAfterLookupError(error: unknown, ean: string): NewFoodFromBarcode | null {
  return isBarcodeNotFound(error) ? newFoodFromBarcode(ean) : null;
}

/**
 * Abrir o cadastro sozinho (consulta sem produto) só se não há formulário aberto: senão o que a pessoa
 * já digitou seria descartado. Com formulário aberto a tela mantém o botão, e só o toque (`force`) troca.
 */
export function shouldOpenRegistration(editingIsOpen: boolean, force: boolean): boolean {
  return force || !editingIsOpen;
}

/** EAN já cadastrado (409 do cadastro): fecha o formulário e seleciona o alimento existente. */
export type UseExistingFood = { selectedId: number; editing: null };

export function existingFoodSelection(foodId: number): UseExistingFood {
  return { selectedId: foodId, editing: null };
}

export function selectionAfterBarcodeConflict(error: unknown): UseExistingFood | null {
  const conflito = barcodeConflict(error);
  return conflito ? existingFoodSelection(conflito.foodId) : null;
}
