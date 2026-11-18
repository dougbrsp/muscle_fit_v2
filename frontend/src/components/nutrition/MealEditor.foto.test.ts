// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement, act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { MEAL_PHOTO } from '@/lib/nutrition-constants';
import { editorFormFromPhoto } from '@/lib/nutrition-meal-parse';
import type { MealParseItem, MealPhotoResult } from '@/lib/nutrition-types';
import { MealEditor } from './MealEditor';

// Fluxo revisável da foto do prato (issue #131): o MealEditor é MONTADO (jsdom) com o rascunho que
// `editorFormFromPhoto` devolve. Corrigir alimento, porção, adicionar, remover, cancelar (nenhum POST),
// confirmar (um POST) e rever a foto sem perder edições. Totais vêm do backend (preview fica pendente aqui).

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
        {
          type: 'button',
          'data-testid': 'adicionar',
          onClick: () => onSelect?.({ id: 7, name: 'Aveia', basis: 'g', default_portion: { quantity: 30, unit: 'g' } }),
        },
        'Adicionar Aveia',
      ),
  };
});

const ARROZ = { candidate_id: 3, food_id: 3, name: 'Arroz cozido', score: 1, auto_accept: true, basis: 'g', source: 'taco', brand: null } as const;
const FRANGO = { candidate_id: 410, food_id: 410, name: 'Frango grelhado', score: 0.7, auto_accept: false, basis: 'g', source: 'taco', brand: null } as const;
const WHEY = { name: 'whey', energy_kcal: 400, protein_g: 80, carbohydrates_g: 10, total_fat_g: 5, fiber_g: null };

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

function resultado(jobId: string, items: MealParseItem[]): MealPhotoResult {
  return {
    items,
    limitations: [],
    notes: null,
    job_id: jobId,
    user_id: 2,
    local_date: '2026-11-17',
    meal_slot: 'lunch',
    model: 'm',
    prompt_version: 'p',
    engine_version: 'e',
  } as unknown as MealPhotoResult;
}

const PRIMEIRO = resultado('job-1', [
  lido(0, {}),
  lido(1, { candidates: [FRANGO], food_id: null, match_confidence: null, quantity: 150 }),
  lido(2, { candidates: [], food_id: null, match_confidence: null, quantity: 30, estimate: WHEY, uses_estimate: true }),
]);

const FOTO = new Blob(['jpeg'], { type: 'image/jpeg' });
const form = (r = PRIMEIRO, foto: Blob | null = FOTO) => editorFormFromPhoto(r, { date: '2026-11-17', slot: null }, foto);

let root: Root | null = null;
let host: HTMLElement;
const criadas: string[] = [];
const revogadas: string[] = [];

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
  URL.createObjectURL = () => {
    const url = `blob:foto-${criadas.length}`;
    criadas.push(url);
    return url;
  };
  URL.revokeObjectURL = (url: string) => {
    revogadas.push(url);
  };
});

beforeEach(() => {
  host = document.createElement('div');
  document.body.appendChild(host);
  createMeal.mockResolvedValue({ id: 99 });
});

afterEach(async () => {
  await act(async () => {
    root?.unmount();
  });
  root = null;
  host.remove();
  document.body.innerHTML = '';
  criadas.length = 0;
  revogadas.length = 0;
  vi.clearAllMocks();
});

const esperar = async (vezes = 4) => {
  for (let i = 0; i < vezes; i++) await act(async () => { await new Promise((r) => setTimeout(r, 25)); });
};

const onSaved = vi.fn();
const onCancel = vi.fn();

function arvore(initialForm: ReturnType<typeof form>, chave = 'a') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return createElement(
    QueryClientProvider,
    { client },
    createElement(
      MantineProvider,
      { env: 'test' },
      createElement(MealEditor, { key: chave, date: '2026-11-17', meal: null, initialForm, onSaved, onCancel }),
    ),
  );
}

async function montar(initialForm = form(), chave = 'a') {
  root = createRoot(host);
  await act(async () => {
    root!.render(arvore(initialForm, chave));
  });
  await esperar();
}

const q = (id: string) => document.querySelector(`[data-testid="${id}"]`) as HTMLElement;
const botao = (texto: string) =>
  Array.from(document.querySelectorAll('button')).find((b) => b.textContent?.trim() === texto || b.getAttribute('aria-label') === texto) as HTMLElement | undefined;
const clicarBotao = async (texto: string) => {
  const el = botao(texto);
  expect(el, `botão ${texto}`).toBeDefined();
  await act(async () => { el!.click(); });
  await esperar(2);
};
const quantidades = () =>
  Array.from(host.querySelectorAll('label'))
    .filter((l) => l.textContent?.startsWith('Quantidade'))
    .map((l) => document.getElementById(l.htmlFor) as HTMLInputElement);

async function digitar(el: HTMLInputElement, valor: string) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(el, valor);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

async function escolherFrango() {
  const campo = Array.from(host.querySelectorAll('input')).find((i) => i.getAttribute('placeholder') === 'Escolha o alimento') as HTMLInputElement;
  await act(async () => { campo.click(); campo.focus(); });
  await esperar(2);
  const opcao = Array.from(document.querySelectorAll('[role="option"]')).find((o) => o.textContent?.includes('Frango grelhado')) as HTMLElement;
  expect(opcao).toBeDefined();
  await act(async () => { opcao.click(); });
  await esperar(2);
}

describe('foto do prato: revisão no editor', () => {
  it('corrige o alimento, altera a porção, remove e adiciona; salvar faz um único POST com os itens revisados', async () => {
    await montar();
    await escolherFrango(); // item 2 inferido sem origem: a pessoa escolhe
    await digitar(quantidades()[0], '200'); // porção (quantidade) do arroz
    await clicarBotao('Remover whey'); // remove o item estimado
    await clicarBotao('Adicionar Aveia'); // adiciona por busca
    await clicarBotao('Salvar refeição');

    expect(createMeal).toHaveBeenCalledTimes(1);
    const corpo = createMeal.mock.calls[0][0];
    expect(corpo).toMatchObject({ ai_source: MEAL_PHOTO.meal_source, ai_job_id: 'job-1', meal_slot: 'lunch' });
    expect(corpo.items.map((i: { food_id: number }) => i.food_id)).toEqual([3, 410, 7]);
    expect(corpo.items[0]).toMatchObject({ quantity: 200, unit: 'g' });
    expect(corpo.items[1]).toMatchObject({ quantity: 150 });
    expect(corpo).not.toHaveProperty('photo'); // a foto nunca vai à API
    expect(onSaved).toHaveBeenCalledWith({ id: 99 });
  });

  it('cancelar não grava nada e avisa a tela', async () => {
    await montar();
    await escolherFrango();
    await clicarBotao('Cancelar');
    expect(createMeal).not.toHaveBeenCalled();
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('nova análise (rascunho novo) substitui os itens, nunca soma', async () => {
    await montar();
    expect(quantidades()).toHaveLength(3);
    const segunda = resultado('job-2', [lido(0, { quantity: 80 })]);
    await act(async () => {
      root!.render(arvore(form(segunda, new Blob(['outra'])), 'b'));
    });
    await esperar();
    expect(quantidades().map((i) => i.value)).toEqual(['80']);
  });

  it('o resultado de uma nova análise nasce só com os itens dela', () => {
    const segunda = resultado('job-2', [lido(0, { quantity: 80 }), lido(1, { quantity: 50 })]);
    const novo = form(segunda);
    expect(novo.items.map((i) => i.key)).toEqual(['ia-0', 'ia-1']);
    expect(novo.ai?.job_id).toBe('job-2');
  });

  it('rever a foto não perde as edições; a miniatura some e a URL é revogada ao fechar', async () => {
    await montar();
    await digitar(quantidades()[0], '250');
    expect(host.querySelector('img')).toBeNull();
    await clicarBotao('Ver foto');
    const img = host.querySelector('img') as HTMLImageElement;
    expect(img.getAttribute('src')).toBe(criadas[0]);
    expect(img.alt).toContain('Foto do prato');
    expect(quantidades()[0].value).toBe('250');
    await clicarBotao('Ocultar foto');
    expect(host.querySelector('img')).toBeNull();
    expect(quantidades()[0].value).toBe('250');

    expect(revogadas).toEqual([]);
    await act(async () => { root!.unmount(); });
    root = null;
    expect(revogadas).toEqual([criadas[0]]);
  });

  it('low_confidence: selo só no item marcado; quantidade mantém; trocar o alimento remove; payload sem o campo', async () => {
    const r = resultado('job-3', [
      lido(0, { low_confidence: false }),
      lido(1, { candidates: [FRANGO], food_id: null, match_confidence: null, quantity: 150, low_confidence: true }),
    ]);
    await montar(form(r));
    const selos = () => Array.from(host.querySelectorAll('[data-testid="item-baixa-confianca"]'));
    expect(selos()).toHaveLength(1);
    expect(selos()[0].textContent).toBe('Estimativa a conferir');
    await digitar(quantidades()[1], '170');
    expect(selos()).toHaveLength(1);
    await escolherFrango();
    expect(selos()).toHaveLength(0);
    await clicarBotao('Salvar refeição');
    expect(JSON.stringify(createMeal.mock.calls[0][0])).not.toMatch(/low_?confidence/i);
  });

  it('rascunho sem foto (texto/manual) não mostra a ação', async () => {
    await montar(form(PRIMEIRO, null));
    expect(botao('Ver foto')).toBeUndefined();
    expect(criadas).toHaveLength(0);
  });
});
