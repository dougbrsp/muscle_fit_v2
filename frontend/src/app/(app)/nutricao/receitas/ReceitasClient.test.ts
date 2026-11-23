// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { theme } from '@/theme';
import { ReceitasClient } from './ReceitasClient';

const fetchRecipes = vi.hoisted(() => vi.fn());
const deleteFood = vi.hoisted(() => vi.fn());
const fetchRecipe = vi.hoisted(() => vi.fn());
vi.mock('@/lib/nutrition-api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/nutrition-api')>()),
  fetchRecipes,
  fetchRecipe,
  deleteFood,
}));
vi.mock('@/components/nutrition/NutritionSubnav', () => ({ NutritionSubnav: () => null }));
vi.mock('@/components/nutrition/RecipeEditor', () => ({ RecipeEditor: () => createElement('div', null, 'editor-aberto') }));
vi.mock('@/lib/presentation-slots', () => ({ usePresentationSlot: () => null }));

let root: Root | null = null;
let host: HTMLElement;
let client: QueryClient;

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  window.matchMedia ??= ((q: string) => ({
    matches: false,
    media: q,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
    onchange: null,
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
});
beforeEach(() => {
  fetchRecipes.mockReset();
  deleteFood.mockReset();
  fetchRecipe.mockReset();
});
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  host?.remove();
});

async function mount() {
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
  client = new QueryClient();
  await act(async () => {
    root!.render(
      createElement(QueryClientProvider, { client }, createElement(MantineProvider, { theme }, createElement(ReceitasClient))),
    );
  });
  for (let i = 0; i < 5; i++) await act(async () => new Promise((r) => setTimeout(r, 10)));
}
const settle = () => act(async () => new Promise((r) => setTimeout(r, 20)));
const click = (label: string) =>
  act(async () => {
    [...host.querySelectorAll('button')].find((b) => b.textContent?.includes(label))!.click();
  });
const item = (over = {}) => ({ id: 3, name: 'Bolo', per_100: { energy_kcal: 321 }, serving_size: null, version: 2, version_created_at: null, ...over });

describe('ReceitasClient', () => {
  it('estado vazio explica o que são receitas', async () => {
    fetchRecipes.mockResolvedValue({ items: [] });
    await mount();
    expect(host.textContent).toContain('Você ainda não tem receitas');
  });

  it('lista nome, energia por 100 g e versão do backend', async () => {
    fetchRecipes.mockResolvedValue({ items: [item()] });
    await mount();
    expect(host.textContent).toContain('Bolo');
    expect(host.textContent).toContain('321 kcal por 100 g');
    expect(host.textContent).toContain('versão 2');
  });

  it('erro da lista aparece sem derrubar a tela', async () => {
    fetchRecipes.mockRejectedValue(new Error('x'));
    await mount();
    expect(host.textContent).toContain('Não foi possível carregar as receitas.');
    expect(host.textContent).toContain('Nova receita');
  });

  it('arquivar pede confirmação e usa deleteFood', async () => {
    fetchRecipes.mockResolvedValue({ items: [item()] });
    deleteFood.mockResolvedValue({ status: 'ok', id: 3 });
    await mount();
    await click('Arquivar');
    expect(deleteFood).not.toHaveBeenCalled();
    await click('Confirmar arquivamento');
    expect(deleteFood).toHaveBeenCalledWith(3);
  });

  it('refetch do detalhe que falha não desmonta o editor', async () => {
    fetchRecipes.mockResolvedValue({ items: [item()] });
    fetchRecipe.mockResolvedValueOnce({ id: 3 }).mockRejectedValue(new Error('rede'));
    await mount();
    await click('Editar');
    await settle();
    expect(host.textContent).toContain('editor-aberto');
    await act(async () => {
      await client.refetchQueries({ queryKey: ['nutrition-recipes'] }).catch(() => {});
    });
    expect(fetchRecipe).toHaveBeenCalledTimes(2);
    expect(host.textContent).toContain('editor-aberto');
    expect(host.textContent).not.toContain('Não foi possível carregar a receita.');
  });

  it('erro de arquivar some ao escolher Manter', async () => {
    fetchRecipes.mockResolvedValue({ items: [item()] });
    deleteFood.mockRejectedValue(new Error('x'));
    await mount();
    await click('Arquivar');
    await click('Confirmar arquivamento');
    await settle();
    expect(host.textContent).toContain('Não foi possível arquivar a receita.');
    await click('Manter');
    expect(host.textContent).not.toContain('Não foi possível arquivar a receita.');
  });

  it('erro de arquivar some ao clicar Arquivar em outra linha', async () => {
    fetchRecipes.mockResolvedValue({ items: [item(), item({ id: 4, name: 'Pão' })] });
    deleteFood.mockRejectedValue(new Error('x'));
    await mount();
    await click('Arquivar');
    await click('Confirmar arquivamento');
    await settle();
    expect(host.textContent).toContain('Não foi possível arquivar a receita.');
    await act(async () => {
      (host.querySelector('button[aria-label="Arquivar Pão"]') as HTMLElement).click();
    });
    expect(host.textContent).not.toContain('Não foi possível arquivar a receita.');
  });

  it('Nova receita abre o editor', async () => {
    fetchRecipes.mockResolvedValue({ items: [] });
    await mount();
    await click('Nova receita');
    expect(host.textContent).toContain('editor-aberto');
  });
});
