// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement, act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { MealEditor } from './MealEditor';

// MealEditor com o FoodRegistrationFlow REAL (só o formulário e a busca são trocados por botões): fechar a etapa final
// enquanto o Concluir ainda grava o favorito entrega o alimento UMA vez e não fecha um cadastro reaberto.

const addFavorite = vi.hoisted(() => vi.fn());
vi.mock('@/lib/nutrition-api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/nutrition-api')>()),
  addFavorite,
  fetchLastMealTime: vi.fn().mockResolvedValue({ time: null }),
  previewMeal: vi.fn(() => new Promise(() => undefined)),
}));
vi.mock('./FavoriteShortcuts', () => ({ FavoriteShortcuts: () => null }));
vi.mock('./FoodSearch', async () => {
  const { createElement: h } = await import('react');
  return {
    FoodSearch: ({ onRegister }: { onRegister?: (termo: string) => void }) =>
      h('button', { type: 'button', 'data-testid': 'abrir-cadastro', onClick: () => onRegister?.('iogurte') }, 'Cadastrar'),
  };
});
vi.mock('./FoodForm', async () => {
  const { createElement: h } = await import('react');
  return {
    FoodForm: (p: { onSaved: (f: unknown) => void }) =>
      h(
        'button',
        {
          type: 'button',
          'data-testid': 'salvar-alimento',
          onClick: () => p.onSaved({ id: 41, name: 'Iogurte novo', basis: 'g', default_portion: null, permissions: { can_promote: false }, warnings: [] }),
        },
        'Salvar',
      ),
  };
});
vi.mock('./BarcodeLookup', () => ({ BarcodeLookup: () => null }));
vi.mock('./LabelPhotoUpload', () => ({ LabelPhotoUpload: () => null }));

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

beforeEach(async () => {
  host = document.createElement('div');
  document.body.appendChild(host);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  root = createRoot(host);
  await act(async () => {
    root!.render(
      createElement(
        QueryClientProvider,
        { client },
        createElement(MantineProvider, { env: 'test' }, createElement(MealEditor, { date: '2026-11-04', meal: null, onSaved: vi.fn(), onCancel: vi.fn() })),
      ),
    );
  });
  await esperar();
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

const esperar = async (vezes = 3) => {
  for (let i = 0; i < vezes; i++) await act(async () => { await new Promise((r) => setTimeout(r, 25)); });
};
const q = (id: string) => document.querySelector(`[data-testid="${id}"]`) as HTMLElement | null;
const clicar = async (el: HTMLElement | null) => {
  expect(el).not.toBeNull();
  await act(async () => { el!.click(); });
  await esperar();
};
const concluir = () => clicar(Array.from(document.querySelectorAll('button')).find((b) => b.textContent?.includes('Concluir')) ?? null);
const marcarFavorito = async () => {
  const rotulo = Array.from(document.querySelectorAll('label')).find((l) => l.textContent?.includes('favoritos'))!;
  await clicar(document.getElementById(rotulo.htmlFor));
};
const fecharModal = () => clicar(document.querySelector('.mantine-Modal-close') as HTMLElement | null);
const linhas = () => (document.body.textContent?.match(/Iogurte novo/g) ?? []).length;

describe('MealEditor + Flow real: fechar durante o Concluir', () => {
  it('Concluir com o favorito pendente, fechar e resolver: uma linha só', async () => {
    let resolver: () => void = () => undefined;
    addFavorite.mockImplementation(() => new Promise<void>((r) => { resolver = () => r(); }));
    await clicar(q('abrir-cadastro'));
    await clicar(q('salvar-alimento'));
    await marcarFavorito();
    await concluir(); // addFavorite pendente
    await fecharModal();
    expect(q('etapa-final')).toBeNull();
    expect(linhas()).toBe(1);
    await act(async () => { resolver(); });
    await esperar(4);
    expect(linhas()).toBe(1);
    expect(document.querySelectorAll('label').length > 0).toBe(true);
  });

  it('reabrir o cadastro antes de resolver: a conclusão tardia não o fecha nem limpa a entrega nova', async () => {
    let resolver: () => void = () => undefined;
    addFavorite.mockImplementation(() => new Promise<void>((r) => { resolver = () => r(); }));
    await clicar(q('abrir-cadastro'));
    await clicar(q('salvar-alimento'));
    await marcarFavorito();
    await concluir();
    await fecharModal();
    await clicar(q('abrir-cadastro')); // reaberto
    expect(document.querySelector('.mantine-Modal-close')).not.toBeNull();
    await act(async () => { resolver(); });
    await esperar(4);
    expect(document.querySelector('.mantine-Modal-close')).not.toBeNull(); // continua aberto
    // a entrega do cadastro novo segue valendo: salvar e fechar na etapa final adiciona o segundo item
    await clicar(q('salvar-alimento'));
    await fecharModal();
    expect(linhas()).toBe(2);
  });
});
