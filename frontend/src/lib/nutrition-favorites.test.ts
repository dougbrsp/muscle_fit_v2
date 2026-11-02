import { describe, expect, it } from 'vitest';
import { barcodeConflict } from './nutrition-api';
import { ApiError } from './api';
import {
  editorItemFromFavorite,
  favoriteDefaultFormFrom,
  favoriteDefaultPayload,
  favoriteDefaultSummary,
  favoriteIds,
  favoritesFirst,
  moveFavorite,
} from './nutrition-favorites';
import type { FavoriteItem, FoodItem } from './nutrition-types';

function food(id: number, name = `Alimento ${id}`): FoodItem {
  return { id, name, basis: 'g' } as unknown as FoodItem;
}

function favorite(id: number, padrao: FavoriteItem['default'] = null): FavoriteItem {
  return { food: food(id), position: id, default: padrao, created_at: null, updated_at: null };
}

describe('favoritesFirst', () => {
  it('põe os favoritos antes e mantém a ordem da busca dentro de cada grupo', () => {
    const resultado = [{ id: 1 }, { id: 2 }, { id: 3 }, { id: 4 }];
    const ids = favoriteIds([favorite(4), favorite(2)]);
    expect(favoritesFirst(resultado, ids).map((i) => i.id)).toEqual([2, 4, 1, 3]);
  });

  it('sem favoritos devolve a mesma ordem, sem mutar a entrada', () => {
    const resultado = [{ id: 3 }, { id: 1 }];
    expect(favoritesFirst(resultado, new Set()).map((i) => i.id)).toEqual([3, 1]);
    expect(resultado.map((i) => i.id)).toEqual([3, 1]);
  });
});

describe('moveFavorite', () => {
  it('sobe e desce uma posição', () => {
    expect(moveFavorite([1, 2, 3], 2, -1)).toEqual([2, 1, 3]);
    expect(moveFavorite([1, 2, 3], 2, 1)).toEqual([1, 3, 2]);
  });

  it('nos limites ou com id desconhecido não muda a lista', () => {
    expect(moveFavorite([1, 2, 3], 1, -1)).toEqual([1, 2, 3]);
    expect(moveFavorite([1, 2, 3], 3, 1)).toEqual([1, 2, 3]);
    expect(moveFavorite([1, 2, 3], 99, 1)).toEqual([1, 2, 3]);
  });
});

describe('editorItemFromFavorite', () => {
  it('sem porção padrão entra como um alimento qualquer (quantidade vazia, unidade da base)', () => {
    const item = editorItemFromFavorite(favorite(7), 'novo-1');
    expect(item).toMatchObject({ key: 'novo-1', food_id: 7, quantity: '', unit: 'g', portion_id: null });
  });

  it('com porção padrão leva quantidade, unidade e porção para o preview do motor', () => {
    const item = editorItemFromFavorite(
      favorite(7, { quantity: 2, unit: 'portion', portion_id: 5, portion_label: 'Concha', portion_amount: 140 }),
      'novo-2',
    );
    expect(item).toMatchObject({ food_id: 7, quantity: 2, unit: 'portion', portion_id: 5 });
  });
});

describe('porção padrão (formulário <-> corpo do PUT)', () => {
  it('quantidade vazia e sem unidade limpa a porção padrão', () => {
    expect(favoriteDefaultPayload({ quantity: '', unit: null, portionId: null })).toEqual({
      default_quantity: null,
      default_unit: null,
      default_portion_id: null,
    });
  });

  it('manda o que o usuário escolheu e deixa a validação para a API', () => {
    expect(favoriteDefaultPayload({ quantity: '150', unit: 'g', portionId: null })).toEqual({
      default_quantity: 150,
      default_unit: 'g',
      default_portion_id: null,
    });
    // quantidade sem unidade vai como está: a API responde 400 com o código do motor
    expect(favoriteDefaultPayload({ quantity: 3, unit: null, portionId: null }).default_unit).toBeNull();
    expect(favoriteDefaultPayload({ quantity: '', unit: 'g', portionId: 4 })).toEqual({
      default_quantity: null,
      default_unit: 'g',
      default_portion_id: 4,
    });
  });

  it('o formulário nasce do favorito', () => {
    expect(favoriteDefaultFormFrom(favorite(1))).toEqual({ quantity: '', unit: null, portionId: null });
    expect(
      favoriteDefaultFormFrom(
        favorite(1, { quantity: 1.5, unit: 'portion', portion_id: 9, portion_label: 'x', portion_amount: 10 }),
      ),
    ).toEqual({ quantity: 1.5, unit: 'portion', portionId: 9 });
  });

  it('resumo curto da porção padrão', () => {
    expect(favoriteDefaultSummary(favorite(1))).toBeNull();
    expect(
      favoriteDefaultSummary(
        favorite(1, { quantity: 2, unit: 'portion', portion_id: 9, portion_label: 'Concha', portion_amount: 140 }),
      ),
    ).toBe('2 × Concha');
    expect(
      favoriteDefaultSummary(favorite(1, { quantity: 150, unit: 'g', portion_id: null, portion_label: null, portion_amount: null })),
    ).toContain('150');
  });
});

describe('barcodeConflict', () => {
  it('lê o alimento existente do 409', () => {
    const erro = new ApiError('x', 409, {
      error: 'barcode_already_registered',
      message: 'm',
      existing_food_id: 12,
      existing_food_name: 'Barra',
    });
    expect(barcodeConflict(erro)).toEqual({ foodId: 12, foodName: 'Barra' });
  });

  it('ignora outros erros e corpos malformados', () => {
    expect(barcodeConflict(new ApiError('x', 409, { error: 'outro', existing_food_id: 1 }))).toBeNull();
    expect(barcodeConflict(new ApiError('x', 409, { error: 'barcode_already_registered' }))).toBeNull();
    expect(barcodeConflict(new Error('x'))).toBeNull();
  });
});
