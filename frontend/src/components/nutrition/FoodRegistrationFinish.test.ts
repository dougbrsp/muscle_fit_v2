// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement, act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { FoodItem } from '@/lib/nutrition-types';
import { FoodRegistrationFinish } from './FoodRegistrationFinish';

// Etapa final do cadastro, montada de verdade: favorito e público só rodam em "Concluir"; usuário comum não vê o
// público; falha parcial mantém a etapa aberta, mostra o erro e não refaz o que deu certo.

const addFavorite = vi.hoisted(() => vi.fn());
const promoteFood = vi.hoisted(() => vi.fn());
vi.mock('@/lib/nutrition-api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/nutrition-api')>()),
  addFavorite,
  promoteFood,
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

const alimento = (podePromover: boolean) =>
  ({ id: 5, name: 'Aveia', permissions: { can_promote: podePromover } }) as unknown as Pick<FoodItem, 'id' | 'name' | 'permissions'>;

async function montar(podePromover: boolean, onConclude = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  root = createRoot(host);
  await act(async () => {
    root!.render(
      createElement(
        QueryClientProvider,
        { client },
        createElement(MantineProvider, { env: 'test' }, createElement(FoodRegistrationFinish, { food: alimento(podePromover), onConclude })),
      ),
    );
  });
  await esperar();
  return onConclude;
}

const caixa = (texto: string) =>
  Array.from(host.querySelectorAll('label'))
    .filter((l) => l.textContent?.includes(texto))
    .map((l) => document.getElementById(l.htmlFor) as HTMLInputElement)[0] ?? null;
const marcar = async (texto: string) => {
  const el = caixa(texto);
  expect(el, texto).not.toBeNull();
  await act(async () => { el!.click(); });
};
const concluir = async () => {
  const botao = Array.from(host.querySelectorAll('button')).find((b) => b.textContent?.includes('Concluir'));
  await act(async () => { botao!.click(); });
  await esperar();
};

describe('FoodRegistrationFinish: título', () => {
  it('já no catálogo: título neutro, sem dizer que salvou', async () => {
    const client = new QueryClient();
    root = createRoot(host);
    await act(async () => {
      root!.render(createElement(QueryClientProvider, { client }, createElement(MantineProvider, { env: 'test' },
        createElement(FoodRegistrationFinish, { food: alimento(false), title: 'Alimento encontrado', onConclude: vi.fn() }))));
    });
    await esperar();
    expect(host.textContent).toContain('Alimento encontrado');
    expect(host.textContent).not.toContain('Alimento salvo');
  });
});

describe('FoodRegistrationFinish', () => {
  it('mostra o nome e só chama as ações em Concluir; nada marcado conclui sem chamar a API', async () => {
    const onConclude = await montar(true);
    expect(host.textContent).toContain('Aveia');
    expect(addFavorite).not.toHaveBeenCalled();
    await concluir();
    expect(addFavorite).not.toHaveBeenCalled();
    expect(promoteFood).not.toHaveBeenCalled();
    expect(onConclude).toHaveBeenCalledTimes(1);
  });

  it('usuário comum não vê "cadastro público"', async () => {
    await montar(false);
    expect(caixa('Adicionar aos favoritos')).not.toBeNull();
    expect(caixa('cadastro público')).toBeNull();
  });

  it('admin: favorito e público juntos, com o aviso de visibilidade', async () => {
    addFavorite.mockResolvedValue({});
    promoteFood.mockResolvedValue({});
    const onConclude = await montar(true);
    await marcar('Adicionar aos favoritos');
    await marcar('cadastro público');
    expect(host.textContent).toContain('visível para todos');
    await concluir();
    expect(addFavorite).toHaveBeenCalledWith({ food_id: 5 });
    expect(promoteFood).toHaveBeenCalledWith(5);
    expect(onConclude).toHaveBeenCalledTimes(1);
  });

  it('falha no público: etapa aberta com erro, favorito não é refeito, e concluir de novo funciona', async () => {
    addFavorite.mockResolvedValue({});
    promoteFood.mockRejectedValueOnce(new Error('sem permissão')).mockResolvedValue({});
    const onConclude = await montar(true);
    await marcar('Adicionar aos favoritos');
    await marcar('cadastro público');
    await concluir();
    expect(onConclude).not.toHaveBeenCalled();
    expect(host.querySelector('[role="alert"]')).not.toBeNull();
    expect(host.textContent).toContain('O alimento já está salvo');
    await concluir();
    expect(addFavorite).toHaveBeenCalledTimes(1);
    expect(promoteFood).toHaveBeenCalledTimes(2);
    expect(onConclude).toHaveBeenCalledTimes(1);
  });

  it('falha só no favorito: mantém a etapa e a nova tentativa conclui', async () => {
    addFavorite.mockRejectedValueOnce(new Error('falhou')).mockResolvedValue({});
    const onConclude = await montar(false);
    await marcar('Adicionar aos favoritos');
    await concluir();
    expect(onConclude).not.toHaveBeenCalled();
    await concluir();
    expect(onConclude).toHaveBeenCalledTimes(1);
  });
});
