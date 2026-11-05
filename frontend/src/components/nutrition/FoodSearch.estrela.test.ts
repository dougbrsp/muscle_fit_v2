// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement, act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { FoodSearch } from './FoodSearch';

// Estrela da busca: marcar chama `onFavorited` só depois de gravar o favorito; desmarcar e falha nunca chamam.

const searchFoods = vi.hoisted(() => vi.fn());
const fetchFavorites = vi.hoisted(() => vi.fn());
const addFavorite = vi.hoisted(() => vi.fn());
const removeFavorite = vi.hoisted(() => vi.fn());
vi.mock('@/lib/nutrition-api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/nutrition-api')>()),
  searchFoods,
  fetchFavorites,
  addFavorite,
  removeFavorite,
}));

let root: Root | null = null;
let host: HTMLElement;

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  window.matchMedia ||= ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener() {},
    removeListener() {},
    addEventListener() {},
    removeEventListener() {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
  class RO {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  (globalThis as { ResizeObserver?: unknown }).ResizeObserver ||= RO;
  window.HTMLElement.prototype.scrollIntoView ||= () => {};
});

beforeEach(() => {
  host = document.createElement('div');
  document.body.appendChild(host);
  fetchFavorites.mockResolvedValue({ items: [], max_items: 30 });
  searchFoods.mockResolvedValue({ query: 'aveia', normalized: 'aveia', items: [item(1, false), item(2, true)] });
});

afterEach(async () => {
  await act(async () => {
    root?.unmount();
  });
  root = null;
  host.remove();
  document.body.innerHTML = '';
  vi.clearAllMocks();
});

const esperar = async (vezes = 4) => {
  for (let i = 0; i < vezes; i++) await act(async () => { await new Promise((r) => setTimeout(r, 25)); });
};

const item = (id: number, favorito: boolean) => ({
  id,
  name: `Alimento ${id}`,
  source: 'taco',
  basis: 'g',
  is_favorite: favorito,
  is_public: true,
  is_own: false,
  per_100: {},
  serving: { serving_size: null, household_measure: null, servings_per_package: null },
  field_sources: [],
  citation: null,
  brand: null,
  category: null,
  score: 1,
  auto_accept: false,
});

async function montar(onFavorited?: (i: unknown) => void) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  root = createRoot(host);
  await act(async () => {
    root!.render(
      createElement(
        QueryClientProvider,
        { client },
        createElement(
          MantineProvider,
          { env: 'test' },
          createElement(FoodSearch, { isAdmin: false, selectedId: null, onSelect: vi.fn(), onFavorited }),
        ),
      ),
    );
  });
  const campo = host.querySelector('input') as HTMLInputElement;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(campo, 'aveia');
    campo.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await esperar(20);
}
const estrela = (nome: string) => host.querySelector(`button[aria-label="${nome}"]`) as HTMLButtonElement | null;
const clicar = async (el: HTMLElement | null) => {
  expect(el).not.toBeNull();
  await act(async () => { el!.click(); });
  await esperar();
};

describe('FoodSearch: estrela e onFavorited', () => {
  it('marcar grava o favorito e avisa; desmarcar não avisa', async () => {
    addFavorite.mockResolvedValue({});
    removeFavorite.mockResolvedValue({});
    const onFavorited = vi.fn();
    await montar(onFavorited);
    await clicar(estrela('Favoritar Alimento 1'));
    expect(addFavorite).toHaveBeenCalledWith({ food_id: 1 });
    expect(onFavorited).toHaveBeenCalledTimes(1);
    expect(onFavorited.mock.calls[0][0]).toMatchObject({ id: 1 });
    await clicar(estrela('Remover Alimento 2 dos preferidos'));
    expect(removeFavorite).toHaveBeenCalledWith(2);
    expect(onFavorited).toHaveBeenCalledTimes(1);
  });

  it('se o favorito falhar, não avisa e mostra o erro', async () => {
    addFavorite.mockRejectedValue(new Error('falhou'));
    const onFavorited = vi.fn();
    await montar(onFavorited);
    await clicar(estrela('Favoritar Alimento 1'));
    expect(onFavorited).not.toHaveBeenCalled();
    expect(host.textContent).toContain('Não foi possível');
  });

  it('sem onFavorited (página de alimentos) a estrela só favorita', async () => {
    addFavorite.mockResolvedValue({});
    await montar();
    await clicar(estrela('Favoritar Alimento 1'));
    expect(addFavorite).toHaveBeenCalledTimes(1);
  });
});
