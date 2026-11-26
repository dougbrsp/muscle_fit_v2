// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement, act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { MEAL_PHOTO } from '@/lib/nutrition-constants';
import { editorFormFromPhoto } from '@/lib/nutrition-meal-parse';
import type { MealParseItem, MealPhotoResult } from '@/lib/nutrition-types';
import { MealEditor } from './MealEditor';

// Layout do item no editor (L61, print do iPhone): alimento na linha toda; quantidade, unidade e
// lixeira na mesma linha; selos depois. E "Buscar outro alimento…" troca o alimento pelo catálogo.

const createMeal = vi.hoisted(() => vi.fn());
vi.mock('@/lib/nutrition-api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/nutrition-api')>()),
  createMeal,
  fetchFood: vi.fn().mockResolvedValue({ id: 3, name: 'Arroz', basis: 'g', portions: [] }),
  fetchLastMealTime: vi.fn().mockResolvedValue({ time: null }),
  previewMeal: vi.fn(() => new Promise(() => undefined)),
}));
vi.mock('./FavoriteShortcuts', () => ({ FavoriteShortcuts: () => null }));
vi.mock('./FoodSearch', async () => {
  const { createElement: h } = await import('react');
  return {
    FoodSearch: ({ onSelect }: { onSelect?: (item: unknown) => void }) =>
      h(
        'button',
        { type: 'button', onClick: () => onSelect?.({ id: 7, name: 'Aveia', basis: 'g', default_portion: { quantity: 30, unit: 'g' } }) },
        'Escolher Aveia',
      ),
  };
});

const ARROZ = { candidate_id: 3, food_id: 3, name: 'Arroz cozido', score: 1, auto_accept: true, basis: 'g', source: 'taco', brand: null } as const;

function lido(index: number, extra: Partial<MealParseItem>): MealParseItem {
  return {
    index,
    ai: { name: 'x', quantity: null, unit: 'g', grams_estimate: 100, preparation: null, confidence: 0.9 },
    status: 'selected',
    candidates: [ARROZ],
    food_id: 3,
    match_confidence: 1,
    quantity: 120,
    unit: 'g',
    estimate: null,
    candidate_for_catalog: false,
    uses_estimate: false,
    limitations: [],
    ...extra,
  };
}

const RESULTADO = {
  items: [
    lido(0, { evidence: 'observed', grams_low: 100, grams_high: 140, low_confidence: true } as Partial<MealParseItem>),
    lido(1, { quantity: 80 }),
  ],
  limitations: [],
  notes: null,
  job_id: 'job-l61',
  user_id: 2,
  local_date: '2026-11-26',
  meal_slot: 'lunch',
  model: 'm',
  prompt_version: 'p',
  engine_version: 'e',
} as unknown as MealPhotoResult;

let root: Root | null = null;

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

afterEach(async () => {
  await act(async () => {
    root?.unmount();
  });
  root = null;
  document.body.innerHTML = '';
  vi.clearAllMocks();
});

const aguardar = async (cond: () => unknown) => {
  await act(async () => {
    await vi.waitFor(() => expect(cond()).toBeTruthy(), { timeout: 4000, interval: 25 });
  });
};

async function montar() {
  const host = document.createElement('div');
  document.body.appendChild(host);
  const initialForm = editorFormFromPhoto(RESULTADO, { date: '2026-11-26', slot: null }, []);
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
          createElement(MealEditor, { date: '2026-11-26', meal: null, initialForm, onSaved: vi.fn(), onCancel: vi.fn() }),
        ),
      ),
    );
  });
  await aguardar(() => host.querySelector('[data-testid="item-linha"]'));
  return host;
}

const antes = (a: Element, b: Element) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
const rotulo = (escopo: Element, texto: string) =>
  Array.from(escopo.querySelectorAll('label')).find((l) => l.textContent?.startsWith(texto));
const botao = (texto: string) =>
  Array.from(document.querySelectorAll('button')).find((b) => b.textContent?.trim() === texto) as HTMLElement | undefined;

describe('editor de refeição: layout do item', () => {
  it('alimento na primeira linha; quantidade, unidade e lixeira juntas; selos depois', async () => {
    const host = await montar();
    const linha = host.querySelector('[data-testid="item-linha"]')!;
    const alimento = linha.querySelector('[data-testid="item-alimento"]')!;
    const qtdUnid = linha.querySelector('[data-testid="item-quantidade-unidade"]')!;
    expect(rotulo(alimento, 'Alimento')).toBeDefined();
    expect(rotulo(qtdUnid, 'Quantidade')).toBeDefined();
    expect(rotulo(qtdUnid, 'Unidade')).toBeDefined();
    expect(qtdUnid.querySelector('[aria-label="Remover Arroz cozido"]')).not.toBeNull();
    expect(antes(alimento, qtdUnid)).toBe(true);
    const selos = linha.querySelector('[data-testid="item-selos"]')!;
    const meta = linha.querySelector('[data-testid="item-foto-meta"]')!;
    expect(selos.querySelector('[data-testid="item-baixa-confianca"]')).not.toBeNull();
    expect(antes(qtdUnid, selos)).toBe(true);
    expect(antes(qtdUnid, meta)).toBe(true);
    // Sem largura fixa: os dois campos dividem a linha.
    expect(qtdUnid.innerHTML).not.toMatch(/width:\s*(120|190)px/);
  });

  it('"Buscar outro alimento…" troca o alimento do item pelo do catálogo e conta como edição da foto', async () => {
    const host = await montar();
    const campo = Array.from(host.querySelectorAll('input')).find((i) => i.getAttribute('placeholder') === 'Escolha o alimento') as HTMLInputElement;
    await act(async () => { campo.click(); campo.focus(); });
    const opcao = () => Array.from(document.querySelectorAll('[role="option"]')).find((o) => o.textContent?.includes('Buscar outro alimento')) as HTMLElement | undefined;
    await aguardar(opcao);
    await act(async () => { opcao()!.click(); });
    await aguardar(() => host.querySelector('[data-testid="item-busca-catalogo"]'));
    await act(async () => { (host.querySelector('[data-testid="item-busca-catalogo"] button') as HTMLElement).click(); }); // a busca do item, não a do editor
    await aguardar(() => !host.querySelector('[data-testid="item-busca-catalogo"]'));

    const meta = host.querySelector('[data-testid="item-foto-meta"]')!;
    expect(meta.textContent).toContain(MEAL_PHOTO.evidence_labels[MEAL_PHOTO.edited_item_evidence as keyof typeof MEAL_PHOTO.evidence_labels]);
    expect(host.querySelector('[aria-label="Remover Aveia"]')).not.toBeNull();
    expect(host.querySelector('[aria-label="Remover Arroz cozido"]')).not.toBeNull(); // o outro item segue

    createMeal.mockResolvedValue({ id: 1 });
    await act(async () => { botao('Salvar refeição')!.click(); });
    await aguardar(() => createMeal.mock.calls.length === 1);
    const itens = createMeal.mock.calls[0][0].items;
    expect(itens[0]).toMatchObject({ food_id: 7, quantity: 120, unit: 'g' });
    expect(itens[0]).not.toHaveProperty('estimate');
    expect(itens[1]).toMatchObject({ food_id: 3, quantity: 80 });
  });
});
