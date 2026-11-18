// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { FoodsAlphabet } from './FoodsAlphabet';

const listFoodsByLetter = vi.hoisted(() => vi.fn());
vi.mock('@/lib/nutrition-api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/nutrition-api')>()),
  listFoodsByLetter,
}));

let root: Root | null = null;
let host: HTMLElement;

const LETTERS = ['A', 'B', 'outros'];
const food = (id: number, name: string, brand: string | null = null) => ({ id, name, brand, category: null });
const page = (letter: string, items: unknown[], offset = 0, has_more = false) => ({
  letter,
  letters: LETTERS,
  items,
  limit: 2,
  offset,
  has_more,
});

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
});

beforeEach(() => {
  host = document.createElement('div');
  document.body.appendChild(host);
  listFoodsByLetter.mockReset();
});

afterEach(async () => {
  await act(async () => {
    root?.unmount();
  });
  root = null;
  host.remove();
  document.body.innerHTML = '';
});

async function render(onSelect = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  root = createRoot(host);
  await act(async () => {
    root!.render(
      createElement(QueryClientProvider, { client }, createElement(MantineProvider, null, createElement(FoodsAlphabet, { selectedId: null, onSelect })))
    );
  });
  return onSelect;
}

const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });
const tab = (label: string) =>
  Array.from(host.querySelectorAll('button')).find((b) => b.textContent === label) as HTMLButtonElement;

describe('FoodsAlphabet', () => {
  it('mostra as abas da API, nome e marca, e abre o item ao tocar', async () => {
    listFoodsByLetter.mockResolvedValue(page('A', [food(1, 'Aveia', 'Quaker')]));
    const onSelect = await render();
    await flush();
    expect(Array.from(host.querySelectorAll('[aria-label="Filtrar por letra"] button')).map((b) => b.textContent)).toEqual(LETTERS);
    expect(tab('A').getAttribute('aria-pressed')).toBe('true');
    expect(host.textContent).toContain('Quaker');
    await act(async () => {
      (host.querySelector('[aria-label="Ver Aveia"]') as HTMLElement).click();
    });
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ id: 1 }));
  });

  it('resposta sem items/letters não derruba a seção', async () => {
    listFoodsByLetter.mockResolvedValue({ role: 'regular' });
    await render();
    await flush();
    expect(host.textContent).toContain('Nenhum alimento cadastrado nesta letra.');
    expect(host.textContent).not.toContain('Carregar mais');
  });

  it('troca de letra e mostra o estado vazio', async () => {
    listFoodsByLetter.mockImplementation(async (l: string) => (l === 'A' ? page('A', [food(1, 'Aveia')]) : page(l, [])));
    await render();
    await flush();
    await act(async () => tab('B').click());
    await flush();
    expect(listFoodsByLetter).toHaveBeenLastCalledWith('B', 0);
    expect(tab('B').getAttribute('aria-pressed')).toBe('true');
    expect(host.textContent).toContain('Nenhum alimento cadastrado nesta letra.');
  });

  it('carrega mais acumulando pelo offset', async () => {
    listFoodsByLetter.mockImplementation(async (_l: string, offset = 0) =>
      offset === 0 ? page('A', [food(1, 'Aveia'), food(2, 'Arroz')], 0, true) : page('A', [food(3, 'Atum')], 2, false),
    );
    await render();
    await flush();
    await act(async () => {
      Array.from(host.querySelectorAll('button')).find((b) => b.textContent === 'Carregar mais')!.click();
    });
    await flush();
    expect(listFoodsByLetter).toHaveBeenLastCalledWith('A', 2);
    expect(['Aveia', 'Arroz', 'Atum'].every((n) => host.textContent?.includes(n))).toBe(true);
    expect(host.textContent).not.toContain('Carregar mais');
  });

  const retryBtn = () => Array.from(host.querySelectorAll('button')).find((b) => b.textContent === 'Tentar de novo');
  const tabLabels = () =>
    Array.from(host.querySelectorAll('[aria-label="Filtrar por letra"] button')).map((b) => b.textContent);

  it('erro ao trocar de letra mantém as abas, mostra o erro e não mostra itens da anterior; tentar de novo recupera', async () => {
    let falha = true;
    listFoodsByLetter.mockImplementation(async (l: string) => {
      if (l === 'A') return page('A', [food(1, 'Aveia')]);
      if (falha) throw new Error('boom');
      return page('B', [food(2, 'Banana')]);
    });
    await render();
    await flush();
    await act(async () => tab('B').click());
    await flush();
    expect(tabLabels()).toEqual(LETTERS);
    expect(tab('B').getAttribute('aria-pressed')).toBe('true');
    expect(host.querySelector('[role="alert"]')).not.toBeNull();
    expect(host.textContent).not.toContain('Aveia');
    falha = false;
    await act(async () => retryBtn()!.click());
    await flush();
    expect(host.querySelector('[role="alert"]')).toBeNull();
    expect(host.textContent).toContain('Banana');
  });

  it('falha na primeira carga mostra erro com Tentar de novo e sem abas', async () => {
    listFoodsByLetter.mockRejectedValueOnce(new Error('boom')).mockResolvedValue(page('A', [food(1, 'Aveia')]));
    await render();
    await flush();
    expect(tabLabels()).toEqual([]);
    expect(retryBtn()).toBeTruthy();
    await act(async () => retryBtn()!.click());
    await flush();
    expect(tabLabels()).toEqual(LETTERS);
    expect(host.textContent).toContain('Aveia');
  });

  it('item repetido entre páginas aparece uma vez', async () => {
    listFoodsByLetter.mockImplementation(async (_l: string, offset = 0) =>
      offset === 0 ? page('A', [food(1, 'Aveia'), food(2, 'Arroz')], 0, true) : page('A', [food(2, 'Arroz'), food(3, 'Atum')], 2, false),
    );
    await render();
    await flush();
    await act(async () => {
      Array.from(host.querySelectorAll('button')).find((b) => b.textContent === 'Carregar mais')!.click();
    });
    await flush();
    expect(host.querySelectorAll('[aria-label="Ver Arroz"]').length).toBe(1);
    expect(host.querySelectorAll('li').length).toBe(3);
  });
});
