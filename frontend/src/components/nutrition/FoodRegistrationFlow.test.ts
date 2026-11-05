// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement, act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { FoodRegistrationFlow } from './FoodRegistrationFlow';

// Fiação da etapa final: digitação/rótulo (onSaved do FoodForm) e código de barras/USDA (onFound) terminam na
// etapa final no lugar das abas; só "Concluir" entrega o alimento ao registro (onSaved/onFound do fluxo).

const addFavorite = vi.hoisted(() => vi.fn());
const promoteFood = vi.hoisted(() => vi.fn());
vi.mock('@/lib/nutrition-api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/nutrition-api')>()),
  addFavorite,
  promoteFood,
}));

const FOOD = { id: 41, name: 'Iogurte', basis: 'g', default_portion: null, permissions: { can_promote: true } };

vi.mock('./FoodForm', async () => {
  const { createElement: h } = await import('react');
  return {
    FoodForm: (p: { onSaved: (f: unknown) => void; barcode?: string; createSource?: string }) =>
      h(
        'button',
        { type: 'button', 'data-testid': p.createSource ? 'salvar-rotulo' : 'salvar-digitacao', 'data-barcode': p.barcode ?? '', onClick: () => p.onSaved(FOOD) },
        'Salvar',
      ),
  };
});
vi.mock('./BarcodeLookup', async () => {
  const { createElement: h } = await import('react');
  return {
    BarcodeLookup: (p: { onFound: (f: unknown) => void }) =>
      h('button', { type: 'button', 'data-testid': 'achar-codigo', onClick: () => p.onFound(FOOD) }, 'Achar'),
  };
});
vi.mock('./LabelPhotoUpload', async () => {
  const { createElement: h } = await import('react');
  const leitura = {
    readable: true, legibility: 'good', barcode: '7891000100103', food: { name: 'Barra' }, per_serving: {},
    errors: [], warnings: [], ai_notes: [],
  };
  return {
    LabelPhotoUpload: (p: { onRead: (r: unknown) => void }) =>
      h('button', { type: 'button', 'data-testid': 'ler-rotulo', onClick: () => p.onRead(leitura) }, 'Ler'),
  };
});
vi.mock('./UsdaSearch', () => ({ UsdaSearch: () => null }));

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

async function montar() {
  const props = { onSaved: vi.fn(), onFound: vi.fn(), onUseExisting: vi.fn(), onCancel: vi.fn(), onPendingDelivery: vi.fn() };
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  root = createRoot(host);
  await act(async () => {
    root!.render(
      createElement(QueryClientProvider, { client }, createElement(MantineProvider, { env: 'test' }, createElement(FoodRegistrationFlow, props))),
    );
  });
  await esperar();
  return props;
}
const concluir = () => clicar(Array.from(host.querySelectorAll('button')).find((b) => b.textContent?.includes('Concluir')) ?? null);

describe('FoodRegistrationFlow: EAN lido no rótulo', () => {
  it('o código lido pela IA chega ao formulário do rótulo (editável), em vez de ser descartado', async () => {
    const props = await montar();
    const aba = Array.from(host.querySelectorAll('[role="tab"]')).find((t) => t.textContent?.includes('Rótulo')) as HTMLElement;
    await clicar(aba);
    await clicar(q('ler-rotulo'));
    expect(q('salvar-rotulo')?.getAttribute('data-barcode')).toBe('7891000100103');
    await clicar(q('salvar-rotulo'));
    expect(q('etapa-final')).not.toBeNull();
    expect(props.onSaved).not.toHaveBeenCalled();
  });
});

describe('FoodRegistrationFlow: etapa final', () => {
  it('digitação/rótulo: salvar abre a etapa final no lugar das abas; só Concluir entrega o alimento', async () => {
    const props = await montar();
    await clicar(q('salvar-digitacao'));
    expect(q('etapa-final')).not.toBeNull();
    expect(host.querySelector('[role="tablist"]')).toBeNull();
    expect(props.onSaved).not.toHaveBeenCalled();
    await concluir();
    expect(props.onSaved).toHaveBeenCalledWith(FOOD);
    expect(props.onFound).not.toHaveBeenCalled();
  });

  it('código de barras/USDA: achar abre a etapa final; Concluir entrega pelo onFound e marcar favorito grava antes', async () => {
    addFavorite.mockResolvedValue({});
    const props = await montar();
    const aba = Array.from(host.querySelectorAll('[role="tab"]')).find((t) => t.textContent?.includes('Código de barras')) as HTMLElement;
    await clicar(aba);
    await clicar(q('achar-codigo'));
    expect(q('etapa-final')).not.toBeNull();
    expect(props.onPendingDelivery).toHaveBeenCalledWith(expect.any(Function));
    const favorito = Array.from(host.querySelectorAll('label')).find((l) => l.textContent?.includes('favoritos'))!;
    await clicar(document.getElementById(favorito.htmlFor));
    await concluir();
    expect(addFavorite).toHaveBeenCalledWith({ food_id: 41 });
    expect(props.onFound).toHaveBeenCalledWith(FOOD);
    expect(props.onPendingDelivery).toHaveBeenLastCalledWith(null);
  });
});
