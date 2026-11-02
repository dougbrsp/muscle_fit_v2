import { describe, expect, it } from 'vitest';
import { ApiError } from './api';
import {
  editingAfterLookupError,
  existingFoodSelection,
  isBarcodeNotFound,
  newFoodFromBarcode,
  selectionAfterBarcodeConflict,
} from './nutrition-barcode-flow';

const erro = (status: number, code: string, extra: Record<string, unknown> = {}) =>
  new ApiError('x', status, { error: code, message: 'm', ...extra });

describe('EAN não encontrado abre o cadastro com o código', () => {
  it('produto desconhecido, dados inutilizáveis ou linha indisponível abrem o cadastro direto', () => {
    for (const code of ['off_not_found', 'invalid_external_food', 'food_not_found']) {
      expect(isBarcodeNotFound(erro(404, code))).toBe(true);
      expect(editingAfterLookupError(erro(404, code), ' 7891000100103 ')).toEqual({ mode: 'new', barcode: '7891000100103' });
    }
  });

  it('outros erros (EAN inválido, limite diário, rede) não abrem o cadastro', () => {
    for (const code of ['invalid_barcode', 'off_daily_limit', 'off_timeout']) {
      expect(editingAfterLookupError(erro(400, code), '7891000100103')).toBeNull();
    }
    expect(editingAfterLookupError(new Error('x'), '7891000100103')).toBeNull();
  });

  it('estado inicial a partir do EAN: vazio não abre nada', () => {
    expect(newFoodFromBarcode('')).toBeNull();
    expect(newFoodFromBarcode('   ')).toBeNull();
    expect(newFoodFromBarcode(null)).toBeNull();
    expect(newFoodFromBarcode('7891000100103')).toEqual({ mode: 'new', barcode: '7891000100103' });
  });
});

describe('409 de EAN já cadastrado -> usar o alimento existente', () => {
  it('seleciona o existente e fecha o formulário', () => {
    const conflito = erro(409, 'barcode_already_registered', { existing_food_id: 12, existing_food_name: 'Barra' });
    expect(selectionAfterBarcodeConflict(conflito)).toEqual({ selectedId: 12, editing: null });
    expect(existingFoodSelection(5)).toEqual({ selectedId: 5, editing: null });
  });

  it('outro erro não vira seleção', () => {
    expect(selectionAfterBarcodeConflict(erro(409, 'outro', { existing_food_id: 12 }))).toBeNull();
  });
});
