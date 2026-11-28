// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement, act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { editorItemFromFood, type MealEditorForm } from '@/lib/nutrition-meal-form';
import { MealEditor } from './MealEditor';

// Quantidade: o texto digitado fica num rascunho do ItemRow; o `form` (e o preview do motor) só mudam no blur/Enter,
// e o Salvar usa o valor ainda em foco. O cálculo segue na API (previewMeal é mock).

const previewMeal = vi.hoisted(() => vi.fn());
const createMeal = vi.hoisted(() => vi.fn());
vi.mock('@/lib/nutrition-api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/nutrition-api')>()),
  fetchLastMealTime: vi.fn(async () => ({ time: null })),
  previewMeal,
  createMeal,
}));
vi.mock('./FavoriteShortcuts', () => ({ FavoriteShortcuts: () => null }));
vi.mock('./FoodSearch', () => ({ FoodSearch: () => null }));
vi.mock('./MealCard', async () => {
  const { createElement: h } = await import('react');
  return {
    MealItemsTable: () => h('div', { 'data-testid': 'motor' }, 'Tabela do motor'),
    TotalsBadges: () => null,
    TotalsLine: () => null,
  };
});

const RASCUNHO: MealEditorForm = {
  local_date: '2026-11-04',
  meal_slot: 'lunch',
  eaten_at: '12:30',
  notes: '',
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
  Element.prototype.scrollIntoView ||= () => {}; // o Select do Mantine rola até a opção ativa
});

async function montar(form: MealEditorForm) {
  root?.unmount();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const editor = createElement(MealEditor, { date: '2026-11-04', meal: null, initialForm: form, onSaved: vi.fn(), onCancel: vi.fn() });
  root = createRoot(host);
  await act(async () => {
    root!.render(createElement(QueryClientProvider, { client }, createElement(MantineProvider, { env: 'test' }, editor)));
  });
  await esperar(); // passa o debounce de 400 ms: o preview inicial já foi chamado
  previewMeal.mockClear();
}

beforeEach(async () => {
  host = document.createElement('div');
  document.body.appendChild(host);
  previewMeal.mockResolvedValue({ items: [], totals: {} });
  createMeal.mockResolvedValue({ id: 9 });
  await montar(RASCUNHO);
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

const esperar = async (ms = 500) => {
  await act(async () => {
    await new Promise((r) => setTimeout(r, ms));
  });
};

const campo = () => {
  const label = Array.from(host.querySelectorAll('label')).find((l) => l.textContent?.startsWith('Quantidade'))!;
  return document.getElementById(label.htmlFor) as HTMLInputElement;
};

async function digitar(el: HTMLInputElement, valor: string) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(el, valor);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
}
async function colar(el: HTMLInputElement, texto: string) {
  await act(async () => {
    const ev = new Event('paste', { bubbles: true, cancelable: true });
    Object.defineProperty(ev, 'clipboardData', { value: { getData: () => texto } });
    el.dispatchEvent(ev);
  });
}
const focar = async (el: HTMLInputElement) => act(async () => el.focus());
const desfocar = async (el: HTMLInputElement) => act(async () => el.blur());
const botaoSalvar = () => Array.from(host.querySelectorAll('button')).find((b) => b.textContent?.includes('Salvar refeição'))!;

describe('MealEditor: quantidade confirmada só no blur', () => {
  it('digitar vários caracteres não chama o preview e mantém o texto; o blur chama uma vez com o valor final', async () => {
    const el = campo();
    await focar(el);
    for (const texto of ['2', '20', '200']) await digitar(el, texto);
    await esperar();
    expect(previewMeal).not.toHaveBeenCalled();
    expect(campo().value).toBe('200');

    await desfocar(campo());
    await esperar();
    expect(previewMeal).toHaveBeenCalledTimes(1);
    expect(previewMeal.mock.calls[0][0].items[0].quantity).toBe(200);
    expect(campo().value).toBe('200');
    expect(host.querySelector('[data-testid="motor"]')).not.toBeNull();
  });

  it('teclado decimal: inputmode="decimal"; 1,5 digitado grava 1.5 no preview e no salvar; 1, intermediário não dá erro em foco', async () => {
    const el = campo();
    expect(el.getAttribute('inputmode')).toBe('decimal');
    await focar(el);
    await digitar(el, '1,');
    await esperar();
    expect(host.textContent).not.toContain('Informe a quantidade');
    expect(previewMeal).not.toHaveBeenCalled();
    await digitar(campo(), '1,5');
    expect(campo().value).toBe('1,5');
    await desfocar(campo());
    await esperar();
    expect(host.textContent).not.toContain('Informe a quantidade');
    expect(previewMeal).toHaveBeenCalledTimes(1);
    expect(previewMeal.mock.calls[0][0].items[0].quantity).toBe(1.5);
    expect(campo().value).toBe('1,5');
    await act(async () => botaoSalvar().click());
    await esperar(50);
    expect(createMeal.mock.calls[0][0].items[0].quantity).toBe(1.5);
  });

  it.each([['1.5', 1.5, '1,5'], ['1,5', 1.5, '1,5'], ['0.25', 0.25, '0,25'], ['120', 120, '120']])(
    'colar %s grava %s (não 10x): preview após blur e salvar',
    async (colado, esperado, exibido) => {
      const el = campo();
      await focar(el);
      await colar(el, colado);
      expect(campo().value).toBe(exibido);
      await desfocar(campo());
      await esperar();
      expect(previewMeal.mock.calls[0][0].items[0].quantity).toBe(esperado);
      await act(async () => botaoSalvar().click());
      await esperar(50);
      expect(createMeal.mock.calls[0][0].items[0].quantity).toBe(esperado);
    },
  );

  it('colar texto não reconhecido (abc, milhar, mais de 3 casas) é bloqueado e não altera o valor', async () => {
    const el = campo();
    await focar(el);
    for (const texto of ['abc', '1.234,5', '1,234.5', '0.12345']) {
      const ev = new Event('paste', { bubbles: true, cancelable: true });
      Object.defineProperty(ev, 'clipboardData', { value: { getData: () => texto } });
      await act(async () => {
        el.dispatchEvent(ev);
      });
      expect(ev.defaultPrevented).toBe(true);
      expect(campo().value).toBe('150');
    }
    expect(campo().value).toBe('150');
  });

  it('0,25 e salvar com o campo em foco gravam 0.25', async () => {
    const el = campo();
    await focar(el);
    await digitar(el, '0,25');
    await act(async () => botaoSalvar().click());
    await esperar(50);
    expect(createMeal.mock.calls[0][0].items[0].quantity).toBe(0.25);
  });

  it('o bloco do motor continua visível enquanto o novo preview carrega', async () => {
    expect(host.querySelector('[data-testid="motor"]')).not.toBeNull();
    previewMeal.mockImplementation(() => new Promise(() => undefined));
    const el = campo();
    await focar(el);
    await digitar(el, '300');
    await desfocar(campo());
    await esperar();
    expect(previewMeal).toHaveBeenCalledTimes(1);
    expect(host.querySelector('[data-testid="motor"]')).not.toBeNull();
    const bloco = host.querySelector('[aria-busy="true"]') as HTMLElement;
    expect(bloco).not.toBeNull();
    expect(bloco.style.opacity).toBe('0.7');
    expect(bloco.querySelector('[role="status"]')?.textContent).toBe('Recalculando…');
  });

  it('salvar com o campo ainda em foco envia a quantidade final', async () => {
    const el = campo();
    await focar(el);
    await digitar(el, '250');
    await act(async () => botaoSalvar().click());
    await esperar(50);
    expect(createMeal).toHaveBeenCalledTimes(1);
    expect(createMeal.mock.calls[0][0].items[0].quantity).toBe(250);
  });

  it('valor vazio mostra erro no campo, preserva o texto e não chama o preview', async () => {
    const el = campo();
    await focar(el);
    await digitar(el, '');
    await desfocar(campo());
    await esperar();
    expect(host.textContent).toContain('Informe a quantidade');
    expect(campo().value).toBe('');
    expect(previewMeal).not.toHaveBeenCalled();
  });

  it('campo vazio e troca para a estimativa da IA: o campo mostra a quantidade nova e o salvar a envia', async () => {
    const lido = {
      ...editorItemFromFood({ id: 1, name: 'Arroz', basis: 'g' }, 'k-1'),
      quantity: 150,
      choices: [{ food_id: 1, name: 'Arroz', brand: null, basis: 'g', score: 0.9 }],
      estimate: { name: 'Prato estimado', energy_kcal: 100, protein_g: 1, carbohydrates_g: 1, total_fat_g: 1, fiber_g: 1 },
      proposal: { quantity: 80, unit: 'g' },
    } as unknown as MealEditorForm['items'][number];
    await montar({ ...RASCUNHO, items: [lido] });
    const el = campo();
    await focar(el);
    await digitar(el, '');
    await act(async () => {
      document.querySelector<HTMLInputElement>('input[placeholder="Escolha o alimento"]')!.click();
    });
    const opcao = Array.from(document.querySelectorAll('[role="option"]')).find((o) => o.textContent?.includes('Estimativa da IA'))!;
    await act(async () => (opcao as HTMLElement).click());
    await esperar(50);
    expect(campo().value).toBe('80');
    await act(async () => botaoSalvar().click());
    await esperar(50);
    expect(createMeal).toHaveBeenCalledTimes(1);
    expect(createMeal.mock.calls[0][0].items[0].quantity).toBe(80);
  });

  it('item nascido com quantidade vazia: digitar sem blur e clicar em Salvar grava o valor digitado', async () => {
    await montar({ ...RASCUNHO, items: [{ ...RASCUNHO.items[0], quantity: '' }] });
    expect(botaoSalvar().disabled).toBe(true);
    const el = campo();
    await focar(el);
    await digitar(el, '100');
    expect(botaoSalvar().disabled).toBe(false);
    await act(async () => botaoSalvar().click());
    await esperar(50);
    expect(createMeal).toHaveBeenCalledTimes(1);
    expect(createMeal.mock.calls[0][0].items[0].quantity).toBe(100);
  });

  it('refeição incompleta por outro motivo (sem refeição ou outro item sem quantidade): Salvar não chama a API nem mostra erro genérico', async () => {
    const vazio = { ...RASCUNHO.items[0], quantity: '' as const };
    for (const form of [
      { ...RASCUNHO, meal_slot: null, items: [vazio] },
      { ...RASCUNHO, items: [vazio, { ...vazio, key: 'k-2' }] },
    ] as MealEditorForm[]) {
      await montar(form);
      const el = campo();
      await focar(el);
      await digitar(el, '100');
      await act(async () => botaoSalvar().click());
      await esperar(50);
      expect(createMeal).not.toHaveBeenCalled();
      expect(host.textContent).not.toContain('Refeição incompleta');
      if (form.meal_slot === null) expect(host.textContent).toContain('Escolha a refeição para salvar');
    }
  });

  it('pendente inválido em A e valor válido digitado em B: Salvar habilitado, não grava, erro em A; corrigir A grava A e B', async () => {
    const b = { ...RASCUNHO.items[0], key: 'k-2', label: 'Feijão', quantity: '' as const };
    await montar({ ...RASCUNHO, items: [RASCUNHO.items[0], b] });
    const campos = () =>
      Array.from(host.querySelectorAll('label'))
        .filter((l) => l.textContent?.startsWith('Quantidade'))
        .map((l) => document.getElementById(l.htmlFor) as HTMLInputElement);
    await focar(campos()[0]);
    await digitar(campos()[0], '');
    await desfocar(campos()[0]);
    await focar(campos()[1]);
    await digitar(campos()[1], '100');
    expect(botaoSalvar().disabled).toBe(false);
    await act(async () => botaoSalvar().click());
    await esperar(50);
    expect(createMeal).not.toHaveBeenCalled();
    expect(host.textContent).toContain('Informe a quantidade');
    await focar(campos()[0]);
    await digitar(campos()[0], '150');
    await desfocar(campos()[0]);
    await act(async () => botaoSalvar().click());
    await esperar(50);
    expect(createMeal).toHaveBeenCalledTimes(1);
    const itens = createMeal.mock.calls[0][0].items;
    expect([itens[0].quantity, itens[1].quantity]).toEqual([150, 100]);
  });

  it('remover o item com rascunho pendente tira o pendente: o aviso e o Salvar desabilitado voltam ao normal', async () => {
    const vazio = { ...RASCUNHO.items[0], quantity: '' as const };
    await montar({ ...RASCUNHO, items: [vazio, { ...vazio, key: 'k-2', label: 'Feijão' }] });
    const el = campo();
    await focar(el);
    await digitar(el, '100');
    expect(host.textContent).not.toContain('Informe a quantidade de cada alimento');
    await act(async () => host.querySelector<HTMLElement>('button[aria-label^="Remover"]')!.click());
    expect(botaoSalvar().disabled).toBe(true);
    expect(host.textContent).toContain('Informe a quantidade de cada alimento');
  });

  it('Enter confirma a quantidade: uma única chamada de preview com o valor', async () => {
    const el = campo();
    await focar(el);
    await digitar(el, '175');
    await act(async () => {
      el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    });
    await esperar();
    await desfocar(campo());
    await esperar();
    expect(previewMeal).toHaveBeenCalledTimes(1);
    expect(previewMeal.mock.calls[0][0].items[0].quantity).toBe(175);
  });

  it('salvar com o campo vazio em foco mostra o erro no campo e não grava', async () => {
    const el = campo();
    await focar(el);
    await digitar(el, '');
    await act(async () => botaoSalvar().click());
    await esperar(50);
    expect(host.textContent).toContain('Informe a quantidade');
    expect(host.textContent).not.toContain('Refeição incompleta');
    expect(createMeal).not.toHaveBeenCalled();
  });

  it('blur seguido de clique em Salvar grava o valor confirmado uma única vez', async () => {
    const el = campo();
    await focar(el);
    await digitar(el, '320');
    await desfocar(campo());
    await act(async () => botaoSalvar().click());
    await esperar(50);
    expect(createMeal).toHaveBeenCalledTimes(1);
    expect(createMeal.mock.calls[0][0].items[0].quantity).toBe(320);
  });

  it('erro de rede no preview mantém o valor digitado no campo', async () => {
    previewMeal.mockRejectedValue(new Error('rede'));
    const el = campo();
    await focar(el);
    await digitar(el, '333');
    await desfocar(campo());
    // Espera o preview disparar (atraso interno do componente) em vez de um sleep fixo.
    // (cada passo roda dentro de act: o debounce só dispara quando o React aplica as atualizações)
    for (let i = 0; i < 60 && previewMeal.mock.calls.length === 0; i++) await esperar(50);
    await esperar(50);
    expect(previewMeal).toHaveBeenCalledTimes(1);
    expect(campo().value).toBe('333');
  });

  it('Salvar barrado só por falta de refeição, com o rascunho de quantidade válido: sem o aviso de quantidade', async () => {
    const vazio = { ...RASCUNHO.items[0], quantity: '' as const };
    await montar({ ...RASCUNHO, meal_slot: null, items: [vazio] });
    const el = campo();
    await focar(el);
    await digitar(el, '100');
    await act(async () => botaoSalvar().click());
    await esperar(50);
    expect(createMeal).not.toHaveBeenCalled();
    expect(host.textContent).toContain('Escolha a refeição para salvar');
    expect(host.textContent).not.toContain('Informe a quantidade de cada alimento');
  });

  it('Salvar barrado com outro item ainda sem quantidade: o aviso de quantidade aparece', async () => {
    const vazio = { ...RASCUNHO.items[0], quantity: '' as const };
    await montar({ ...RASCUNHO, meal_slot: null, items: [vazio, { ...vazio, key: 'k-2', label: 'Feijão' }] });
    const el = campo();
    await focar(el);
    await digitar(el, '100');
    await act(async () => botaoSalvar().click());
    await esperar(50);
    expect(createMeal).not.toHaveBeenCalled();
    expect(host.textContent).toContain('Informe a quantidade de cada alimento');
  });
});
