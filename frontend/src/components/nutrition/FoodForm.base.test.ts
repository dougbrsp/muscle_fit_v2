// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement, act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import motorConstants from '@/lib/motor-constants.generated.json';
import { FoodForm } from './FoodForm';

// Base da tabela vazia: a tela bloqueia salvar e mostra o erro no campo (a API trataria o vazio como "por 100",
// errado para uma tabela por 30 g). O seletor de unidade usa as unidades do dono (`nutrition.table_base.units`).

const N = motorConstants.nutrition;

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
});

const esperar = async () => {
  await act(async () => {
    await new Promise((r) => setTimeout(r, 25));
  });
};

async function montar() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  root = createRoot(host);
  await act(async () => {
    root!.render(
      createElement(
        QueryClientProvider,
        { client },
        createElement(MantineProvider, { env: 'test' }, createElement(FoodForm, { food: null, onSaved: vi.fn(), onCancel: vi.fn() })),
      ),
    );
  });
  await esperar();
}

const baseInput = () => {
  const label = Array.from(host.querySelectorAll('label')).find((l) => l.textContent?.startsWith('Valores da tabela por'));
  return document.getElementById(label!.htmlFor) as HTMLInputElement;
};
const salvar = () => Array.from(host.querySelectorAll('button')).find((b) => b.textContent?.includes('Cadastrar alimento')) as HTMLButtonElement;

async function digitarBase(valor: string) {
  const el = baseInput();
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(el, valor);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await esperar();
}

describe('FoodForm: base da tabela', () => {
  it('abre na base padrão do dono, com salvar liberado', async () => {
    await montar();
    expect(Number(baseInput().value.replace(',', '.'))).toBe(N.table_base.default);
    expect(salvar().disabled).toBe(false);
  });

  it('base vazia: salvar bloqueado e erro no campo; voltando a digitar, libera', async () => {
    await montar();
    await digitarBase('');
    expect(salvar().disabled).toBe(true);
    expect(host.textContent).toContain('Informe a quantidade a que os valores da tabela se referem.');
    await digitarBase('30');
    expect(salvar().disabled).toBe(false);
    expect(host.textContent).not.toContain('Informe a quantidade a que os valores da tabela se referem.');
  });

  it('o seletor de unidade da base lista só as unidades do dono (g, ml), sem "por 100"', async () => {
    await montar();
    const unidade = Array.from(host.querySelectorAll('label'))
      .filter((l) => l.textContent?.startsWith('Unidade'))
      .map((l) => document.getElementById(l.htmlFor) as HTMLInputElement)[0];
    expect(unidade.value).toBe(N.table_base.unit_labels[N.default_food_basis as 'g']);
    expect(unidade.value).not.toMatch(/100/);
  });
});
