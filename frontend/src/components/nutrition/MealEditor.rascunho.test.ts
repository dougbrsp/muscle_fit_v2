// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement, act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { editorItemFromFood, type MealEditorForm } from '@/lib/nutrition-meal-form';
import { PresentationProvider, type PresentationSlots } from '@/lib/presentation-slots';
import { MealEditor } from './MealEditor';

// Critério "o rascunho sobrevive ao cadastro": o MealEditor é MONTADO de verdade (jsdom) com a busca e o fluxo de
// cadastro trocados por botões que disparam os mesmos callbacks. Hora, anotação e quantidades digitadas continuam
// depois de abrir/fechar o cadastro, e as 3 vias (salvo, achado por código/USDA, existente) adicionam o item.
// No celular o cadastro é uma folha na pilha de visões: o voltar fecha só a folha.

const fetchFood = vi.hoisted(() => vi.fn());
vi.mock('@/lib/nutrition-api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/nutrition-api')>()),
  fetchFood,
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
vi.mock('./FoodRegistrationFlow', async () => {
  const { createElement: h } = await import('react');
  type Fluxo = {
    onSaved: (f: unknown) => void;
    onFound: (f: unknown) => void;
    onUseExisting: (id: number) => void;
    onCancel: () => void;
  };
  const botao = (id: string, onClick: () => void) => h('button', { type: 'button', 'data-testid': id, onClick }, id);
  return {
    FoodRegistrationFlow: (p: Fluxo) =>
      h(
        'div',
        { 'data-testid': 'fluxo' },
        botao('salvo', () => p.onSaved({ id: 41, name: 'Iogurte salvo', basis: 'g', warnings: [{ code: 'atwater', label: 'Aviso de Atwater' }] })),
        botao('achado', () => p.onFound({ id: 42, name: 'Produto achado', basis: 'g', warnings: [{ code: 'off', label: 'Aviso do Open Food Facts' }] })),
        botao('existente', () => p.onUseExisting(43)),
        botao('cancelar', () => p.onCancel()),
      ),
  };
});

const RASCUNHO: MealEditorForm = {
  local_date: '2026-11-04',
  meal_slot: 'lunch',
  eaten_at: '12:30',
  notes: 'sem sal',
  items: [{ ...editorItemFromFood({ id: 1, name: 'Arroz', basis: 'g' }, 'k-1'), quantity: 150 }],
  ai: null,
};

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
  fetchFood.mockResolvedValue({ id: 43, name: 'Já cadastrado', basis: 'g' });
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

async function montar(celular: boolean) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const editor = createElement(MealEditor, { date: '2026-11-04', meal: null, initialForm: RASCUNHO, onSaved: vi.fn(), onCancel: vi.fn() });
  const slots = { nutritionHeader: () => null } as unknown as Partial<PresentationSlots>;
  const arvore = celular ? createElement(PresentationProvider, { value: slots }, editor) : editor;
  root = createRoot(host);
  await act(async () => {
    root!.render(createElement(QueryClientProvider, { client }, createElement(MantineProvider, { env: 'test' }, arvore)));
  });
  await esperar();
}

const q = (id: string) => document.querySelector(`[data-testid="${id}"]`) as HTMLElement | null;
const clicar = async (id: string) => {
  const el = q(id);
  expect(el, `botão ${id}`).not.toBeNull();
  await act(async () => { el!.click(); });
  await esperar(3);
};
const hora = () => host.querySelector('input[type="time"]') as HTMLInputElement;
const nota = () => host.querySelector('textarea') as HTMLTextAreaElement;
const quantidades = () =>
  Array.from(host.querySelectorAll('label'))
    .filter((l) => l.textContent?.startsWith('Quantidade'))
    .map((l) => (document.getElementById(l.htmlFor) as HTMLInputElement).value);

async function digitar(el: HTMLInputElement | HTMLTextAreaElement, valor: string) {
  const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  await act(async () => {
    Object.getOwnPropertyDescriptor(proto, 'value')!.set!.call(el, valor);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

for (const celular of [false, true]) {
  describe(`MealEditor montado (${celular ? 'celular: folha' : 'desktop: modal'}): o rascunho sobrevive ao cadastro`, () => {
    async function rascunhoEditado() {
      await montar(celular);
      await digitar(hora(), '13:45');
      await digitar(nota(), 'sem sal, com limão');
      expect(hora().value).toBe('13:45');
    }
    function rascunhoIntacto() {
      expect(hora().value).toBe('13:45');
      expect(nota().value).toBe('sem sal, com limão');
      expect(quantidades()[0]).toBe('150');
      expect(host.textContent).toContain('Arroz');
    }

    it('abrir e fechar o cadastro sem salvar não muda nada', async () => {
      await rascunhoEditado();
      await clicar('abrir-cadastro');
      expect(q('fluxo')).not.toBeNull();
      await clicar('cancelar');
      expect(q('fluxo')).toBeNull();
      rascunhoIntacto();
      expect(host.textContent).not.toContain('Iogurte salvo');
    });

    for (const [via, id, nome, aviso] of [
      ['alimento salvo', 'salvo', 'Iogurte salvo', 'Aviso de Atwater'],
      ['produto achado (código/USDA)', 'achado', 'Produto achado', 'Aviso do Open Food Facts'],
      ['alimento existente do EAN', 'existente', 'Já cadastrado', null],
    ] as const) {
      it(`${via}: entra como item, o rascunho segue intacto${aviso ? ' e o aviso do motor aparece' : ''}`, async () => {
        await rascunhoEditado();
        await clicar('abrir-cadastro');
        await clicar(id);
        expect(q('fluxo')).toBeNull();
        expect(host.textContent).toContain(nome);
        rascunhoIntacto();
        if (aviso) expect(host.textContent).toContain(aviso);
      });
    }

    it('alimento existente que não carrega mostra o erro e mantém o fluxo aberto', async () => {
      fetchFood.mockRejectedValue(new Error('falhou'));
      await rascunhoEditado();
      await clicar('abrir-cadastro');
      await clicar('existente');
      expect(q('fluxo')).not.toBeNull();
      expect(document.body.textContent).toContain('Não foi possível abrir o alimento existente.');
      rascunhoIntacto();
    });
  });
}

describe('MealEditor no celular: o voltar fecha só a folha do cadastro', () => {
  it('history.back() com a folha aberta fecha a folha e mantém o editor e o rascunho', async () => {
    await montar(true);
    await digitar(hora(), '13:45');
    await clicar('abrir-cadastro');
    expect(q('fluxo')).not.toBeNull();
    await act(async () => { window.history.back(); });
    await esperar(6);
    expect(q('fluxo')).toBeNull();
    expect(hora().value).toBe('13:45');
    expect(host.textContent).toContain('Arroz');
  });
});
