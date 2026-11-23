import { beforeEach, describe, expect, it, vi } from 'vitest';

const apiFetch = vi.hoisted(() => vi.fn());
vi.mock('./api', async (importOriginal) => ({ ...(await importOriginal<typeof import('./api')>()), apiFetch }));

import { createRecipe, deleteFood, fetchRecipe, fetchRecipes, foodKeys, previewRecipe, searchFoods, updateRecipe } from './nutrition-api';

const corpo = {
  name: 'Bolo',
  ingredients: [{ food_id: 1, quantity: 100, unit: 'g' }],
  final_weight_g: 90,
};

describe('cliente de receitas', () => {
  beforeEach(() => {
    apiFetch.mockReset();
    apiFetch.mockResolvedValue({});
  });

  it('preview, criar e atualizar enviam o corpo no método certo', async () => {
    await previewRecipe(corpo);
    await createRecipe(corpo);
    await updateRecipe(7, corpo);
    expect(apiFetch).toHaveBeenNthCalledWith(1, '/nutrition/recipes/preview', { method: 'POST', body: JSON.stringify(corpo) });
    expect(apiFetch).toHaveBeenNthCalledWith(2, '/nutrition/recipes', { method: 'POST', body: JSON.stringify(corpo) });
    expect(apiFetch).toHaveBeenNthCalledWith(3, '/nutrition/recipes/7', { method: 'PUT', body: JSON.stringify(corpo) });
  });

  it('lista e detalhe são GET; arquivar é o deleteFood', async () => {
    await fetchRecipes();
    await fetchRecipe(7);
    await deleteFood(7);
    expect(apiFetch).toHaveBeenNthCalledWith(1, '/nutrition/recipes');
    expect(apiFetch).toHaveBeenNthCalledWith(2, '/nutrition/recipes/7');
    expect(apiFetch).toHaveBeenNthCalledWith(3, '/nutrition/foods/7', { method: 'DELETE' });
  });

  it('searchFoods envia exclude_recipes só quando pedido', async () => {
    await searchFoods('arroz');
    await searchFoods('arroz', { excludeRecipes: true });
    expect(apiFetch).toHaveBeenNthCalledWith(1, '/nutrition/foods/search?q=arroz');
    expect(apiFetch).toHaveBeenNthCalledWith(2, '/nutrition/foods/search?q=arroz&exclude_recipes=true');
  });

  it('a chave da busca distingue excludeRecipes', () => {
    expect(foodKeys.search('a', 'visible', null)).not.toEqual(foodKeys.search('a', 'visible', null, true));
  });
});
