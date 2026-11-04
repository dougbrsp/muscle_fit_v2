// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement, act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { AlimentosClient } from './AlimentosClient';

// Editar + Salvar não pode apagar o cadastro em andamento: só o salvamento vindo do fluxo de cadastro
// incrementa o sinal de reset. A busca, o formulário de edição e o detalhe são trocados por botões/marcas.

const ALIMENTO = { id: 7, name: 'Arroz', basis: 'g', warnings: [] };
const sinais: number[] = [];

vi.mock('@/lib/api', () => ({ apiFetch: vi.fn(async () => ({ role: 'regular' })), ApiError: class extends Error {} }));
vi.mock('@/lib/nutrition-api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/nutrition-api')>()),
  fetchFood: vi.fn(async () => ({ id: 7, name: 'Arroz', basis: 'g', portions: [] })),
}));
vi.mock('@/components/nutrition/FoodCitationFooter', () => ({ FoodCitationFooter: () => null }));
vi.mock('@/components/nutrition/NutritionSubnav', () => ({ NutritionSubnav: () => null }));
vi.mock('@/components/nutrition/FoodSearch', async () => {
  const { createElement: h } = await import('react');
  return {
    FoodSearch: ({ onSelect }: { onSelect: (i: unknown) => void }) =>
      h('button', { type: 'button', 'data-testid': 'escolher', onClick: () => onSelect({ id: 7 }) }, 'escolher'),
  };
});
vi.mock('@/components/nutrition/FoodDetailCard', async () => {
  const { createElement: h } = await import('react');
  return {
    FoodDetailCard: ({ onEdit }: { onEdit: () => void }) => h('button', { type: 'button', 'data-testid': 'editar', onClick: onEdit }, 'editar'),
  };
});
vi.mock('@/components/nutrition/FoodForm', async () => {
  const { createElement: h } = await import('react');
  return {
    FoodForm: ({ onSaved }: { onSaved: (f: unknown) => void }) =>
      h('button', { type: 'button', 'data-testid': 'salvar-edicao', onClick: () => onSaved(ALIMENTO) }, 'salvar-edicao'),
  };
});
vi.mock('@/components/nutrition/FoodRegistrationFlow', async () => {
  const { createElement: h } = await import('react');
  return {
    FoodRegistrationFlow: ({ resetSignal, onSaved }: { resetSignal?: number; onSaved: (f: unknown) => void }) => {
      sinais.push(resetSignal ?? 0);
      return h('button', { type: 'button', 'data-testid': 'salvar-cadastro', onClick: () => onSaved(ALIMENTO) }, 'salvar-cadastro');
    },
  };
});

let root: Root | null = null;
let host: HTMLElement;
let rolou: ReturnType<typeof vi.fn>;

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  window.matchMedia ||= ((query: string) => ({
    matches: false, media: query, onchange: null, addListener() {}, removeListener() {},
    addEventListener() {}, removeEventListener() {}, dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
  class RO { observe() {} unobserve() {} disconnect() {} }
  (globalThis as { ResizeObserver?: unknown }).ResizeObserver ||= RO;
});

beforeEach(() => {
  sinais.length = 0;
  rolou = vi.fn();
  window.HTMLElement.prototype.scrollIntoView = rolou as unknown as typeof window.HTMLElement.prototype.scrollIntoView;
  host = document.createElement('div');
  document.body.appendChild(host);
});

afterEach(async () => {
  await act(async () => { root?.unmount(); });
  root = null;
  host.remove();
});

const esperar = async (n = 3) => { for (let i = 0; i < n; i++) await act(async () => { await new Promise((r) => setTimeout(r, 25)); }); };
const q = (id: string) => host.querySelector(`[data-testid="${id}"]`) as HTMLElement | null;
const clicar = async (id: string) => {
  expect(q(id), `botão ${id}`).not.toBeNull();
  await act(async () => { q(id)!.click(); });
  await esperar();
};

async function montar() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  root = createRoot(host);
  await act(async () => {
    root!.render(createElement(QueryClientProvider, { client }, createElement(MantineProvider, { env: 'test' }, createElement(AlimentosClient))));
  });
  await esperar();
}

describe('AlimentosClient: cadastro em andamento e rolagem', () => {
  it('Editar + Salvar não incrementa o reset do cadastro; salvar pelo cadastro incrementa', async () => {
    await montar();
    const inicial = sinais[sinais.length - 1];
    await clicar('escolher');
    await clicar('editar');
    await clicar('salvar-edicao');
    expect(sinais[sinais.length - 1]).toBe(inicial);
    await clicar('salvar-cadastro');
    expect(sinais[sinais.length - 1]).toBe(inicial + 1);
  });

  it('escolher um alimento na busca rola até o detalhe', async () => {
    await montar();
    rolou.mockClear();
    await clicar('escolher');
    expect(rolou).toHaveBeenCalled();
    expect(q('editar')).not.toBeNull();
  });
});
