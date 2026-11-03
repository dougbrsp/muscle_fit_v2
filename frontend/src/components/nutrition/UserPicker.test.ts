// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement, act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { UserPicker } from './UserPicker';

// Regressão (2026-11-03): rótulo inicial ("Doug") diferente do rótulo do lookup
// ("Doug (doug)") fazia o Select searchable do Mantine e o UserPicker se
// realimentarem sem fim (React #185, "Maximum update depth exceeded").
// Aqui o componente é MONTADO de verdade (jsdom, efeitos rodando).

const fetchUserLookup = vi.hoisted(() => vi.fn());
vi.mock('@/lib/user-lookup', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/user-lookup')>()),
  fetchUserLookup,
}));

const DOUG = { id: 1, username: 'doug', full_name: 'Doug' };
const ANA = { id: 2, username: 'ana', full_name: 'Ana Souza' };

let root: Root | null = null;
let host: HTMLElement;
let erros: unknown[][];

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
  erros = [];
  vi.spyOn(console, 'error').mockImplementation((...args) => {
    erros.push(args);
  });
  fetchUserLookup.mockImplementation(async (termo: string) => ({
    items: [DOUG, ANA].filter((u) => !termo || `${u.full_name} ${u.username}`.toLowerCase().includes(termo.toLowerCase())),
    total: 2,
  }));
  host = document.createElement('div');
  document.body.appendChild(host);
});

afterEach(async () => {
  await act(async () => {
    root?.unmount();
  });
  root = null;
  host.remove();
  vi.restoreAllMocks();
  fetchUserLookup.mockReset();
});

const esperar = async (vezes = 5) => {
  for (let i = 0; i < vezes; i++) await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
};

async function montar(initial: { id: number; label: string }, onChange = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  root = createRoot(host);
  await act(async () => {
    root!.render(
      createElement(
        QueryClientProvider,
        { client },
        createElement(MantineProvider, null, createElement(UserPicker, { value: initial.id, onChange, initial })),
      ),
    );
  });
  await esperar();
  return onChange;
}

const input = () => host.querySelector('input:not([type="hidden"])') as HTMLInputElement;
const loop = () => erros.filter((e) => e.some((parte) => String(parte).includes('Maximum update depth')));

async function digitar(texto: string) {
  const el = input();
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')!.set!;
    setter.call(el, texto);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await esperar(3);
}

describe('UserPicker montado (efeitos rodando)', () => {
  it('rótulos iguais: abre estável e mostra o aluno', async () => {
    await montar({ id: 1, label: 'Doug (doug)' });
    expect(loop()).toEqual([]);
    expect(input().value).toBe('Doug (doug)');
  });

  it('rótulo inicial divergente do lookup: sem ciclo, aluno continua visível', async () => {
    await montar({ id: 1, label: 'Doug' });
    expect(loop()).toEqual([]);
    expect(input().value).toBe('Doug');
    expect(fetchUserLookup.mock.calls.length).toBeLessThan(6);
  });

  it('lookup renomeou o aluno depois do rótulo inicial: sem ciclo, rótulo escolhido preservado', async () => {
    fetchUserLookup.mockImplementation(async () => ({ items: [{ ...DOUG, full_name: 'Douglas Silva' }, ANA], total: 2 }));
    await montar({ id: 1, label: 'Doug (doug)' });
    expect(loop()).toEqual([]);
    expect(input().value).toBe('Doug (doug)');
  });

  it('busca digitada consulta o servidor com o texto', async () => {
    await montar({ id: 1, label: 'Doug' });
    await digitar('ana');
    expect(fetchUserLookup).toHaveBeenCalledWith('ana');
    expect(loop()).toEqual([]);
  });

  it('limpar devolve null', async () => {
    const onChange = await montar({ id: 1, label: 'Doug' });
    const limpar = host.querySelector('[class*="ClearButton"], [class*="CloseButton"]') as HTMLElement | null;
    expect(limpar).not.toBeNull();
    await act(async () => {
      limpar!.click();
    });
    await esperar(2);
    expect(onChange).toHaveBeenCalledWith(null);
    expect(loop()).toEqual([]);
  });
});
