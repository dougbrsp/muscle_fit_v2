// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '@/lib/api';
import { theme } from '@/theme';
import { RecipeEditor } from './RecipeEditor';

const previewRecipe = vi.hoisted(() => vi.fn());
const createRecipe = vi.hoisted(() => vi.fn());
const updateRecipe = vi.hoisted(() => vi.fn());
const fetchRecipeVersions = vi.hoisted(() => vi.fn());
const fetchFood = vi.hoisted(() => vi.fn(async () => ({ id: 9, name: 'Leite', portions: [], basis: 'g' })));
vi.mock('@/lib/nutrition-api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/nutrition-api')>()),
  previewRecipe,
  createRecipe,
  updateRecipe,
  fetchRecipeVersions,
  fetchFood,
}));
vi.mock('./FoodSearch', () => ({
  FoodSearch: (p: { excludeRecipes?: boolean; onSelect: (i: unknown) => void; onRegister?: (t: string) => void }) =>
    createElement(
      'div',
      null,
      createElement(
        'button',
        { 'data-exclude': String(p.excludeRecipes), onClick: () => p.onSelect({ id: 5, name: 'Farinha', basis: 'g' }) },
        'add-farinha',
      ),
      createElement('button', { onClick: () => p.onRegister?.('iogurte') }, 'abrir-cadastro'),
    ),
}));
vi.mock('./FoodRegistrationFlow', () => ({
  FoodRegistrationFlow: (p: { initialName?: string; onSaved: (f: unknown) => void; onUseExisting: (id: number) => void }) =>
    createElement(
      'div',
      null,
      createElement('button', { onClick: () => p.onSaved({ id: 8, name: p.initialName, basis: 'g' }) }, 'salvar-cadastro'),
      createElement('button', { onClick: () => p.onUseExisting(9) }, 'usar-existente'),
    ),
}));

let root: Root | null = null;
let host: HTMLElement;

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  window.matchMedia ??= ((q: string) => ({
    matches: false,
    media: q,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
    onchange: null,
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
});
beforeEach(() => {
  previewRecipe.mockReset();
  createRecipe.mockReset();
  updateRecipe.mockReset();
  fetchRecipeVersions.mockReset();
  fetchRecipeVersions.mockResolvedValue({ items: [] });
});
afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  host?.remove();
});

async function mount(props: Partial<Parameters<typeof RecipeEditor>[0]> = {}) {
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
  const el = createElement(RecipeEditor, { recipe: null, onSaved: () => {}, onCancel: () => {}, ...props });
  await act(async () => {
    root!.render(createElement(QueryClientProvider, { client: new QueryClient() }, createElement(MantineProvider, { theme }, el)));
  });
}
const flush = () => act(async () => { await new Promise((r) => setTimeout(r, 20)); });
const click = (label: string) =>
  act(async () => {
    [...host.querySelectorAll('button')].find((b) => b.textContent?.includes(label))!.click();
  });
async function type(el: Element, value: string) {
  await act(async () => {
    const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto, 'value')!.set!.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
}
function byLabel(text: string): Element {
  const label = [...host.querySelectorAll('label')].find((l) => l.textContent?.startsWith(text))!;
  return document.getElementById(label.htmlFor)!;
}

const previa = (over: Record<string, unknown> = {}) => ({
  ingredients: [],
  totals: { energy_kcal: 777 },
  per_100_calculated: { energy_kcal: 321 },
  per_100_effective: { energy_kcal: 321 },
  overridden_fields: [],
  raw_weight_g: 250,
  final_weight_g: 200,
  yield_percent: null,
  incomplete_fields: [],
  missing_by_field: {},
  atwater: null,
  limitations: [],
  warnings: [],
  engine_version: 'v',
  ...over,
});

async function preencher() {
  await click('add-farinha');
  await type(byLabel('Quantidade'), '100');
  await type(byLabel('Peso final pronto'), '90');
}

describe('RecipeEditor', () => {
  it('busca sem receitas, aviso de cru e modo de preparo como texto puro', async () => {
    await mount();
    expect(host.querySelector('[data-exclude="true"]')).not.toBeNull();
    expect(host.textContent).toContain('ingrediente cru');
    await type(host.querySelector('textarea')!, '<b>misture</b>');
    expect(host.querySelector('textarea')!.value).toBe('<b>misture</b>');
    expect(host.querySelector('b')).toBeNull();
  });

  it('prévia mostra os valores do backend e oculta rendimento nulo', async () => {
    previewRecipe.mockResolvedValue(previa());
    await mount();
    await preencher();
    await click('Calcular');
    expect(previewRecipe).toHaveBeenCalledWith(
      expect.objectContaining({ final_weight_g: 90, ingredients: [{ food_id: 5, quantity: 100, unit: 'g', portion_id: null }] }),
    );
    const html = host.querySelector('[data-testid="previa"]')!.textContent!;
    expect(html).toContain('777');
    expect(html).toContain('321');
    expect(html).toContain('Peso cru: 250');
    expect(html).not.toContain('Rendimento');
  });

  it('mostra o rendimento quando vem e o calculado ao lado do ajustado', async () => {
    previewRecipe.mockResolvedValue(previa({ yield_percent: 80, overridden_fields: ['energy_kcal'], per_100_effective: { energy_kcal: 300 } }));
    await mount();
    await preencher();
    await click('Calcular');
    const html = host.querySelector('[data-testid="previa"]')!.textContent!;
    expect(html).toContain('Rendimento: 80%');
    expect(html).toContain('ajustado (calculado: 321)');
  });

  it('envia o ajuste manual em overrides ao salvar', async () => {
    previewRecipe.mockResolvedValue(previa());
    createRecipe.mockResolvedValue({ id: 1 });
    const onSaved = vi.fn();
    await mount({ onSaved });
    await preencher();
    await type(byLabel('Nome da receita'), 'Bolo');
    await click('Calcular');
    await type(host.querySelector('input[aria-label^="Ajustar "]')!, '250');
    await click('Salvar receita');
    expect(createRecipe).toHaveBeenCalledWith(expect.objectContaining({ name: 'Bolo', overrides: { energy_kcal: 250 } }));
    expect(onSaved).toHaveBeenCalled();
  });

  it('erro de ingrediente aparece na linha, sem derrubar a tela', async () => {
    const msg = 'Ingrediente 1 (Farinha): é uma receita.';
    createRecipe.mockRejectedValue(
      new ApiError(msg, 422, { error: 'ingredient_is_recipe:0', code: 'ingredient_is_recipe:0', message: msg, index: 0 }),
    );
    await mount();
    await preencher();
    await type(byLabel('Nome da receita'), 'Bolo');
    await click('Salvar receita');
    await flush();
    const linha = host.querySelector('[data-testid="ingrediente"]')!;
    expect(linha.querySelector('[role="alert"]')!.textContent).toContain('é uma receita');
    expect(host.textContent).toContain('Salvar receita');
  });

  it('remover ingrediente limpa o erro de linha que ficaria na linha errada', async () => {
    const msg = 'Ingrediente 1 (Farinha): é uma receita.';
    createRecipe.mockRejectedValue(new ApiError(msg, 422, { error: 'x:0', code: 'x:0', message: msg, index: 0 }));
    await mount();
    await preencher();
    await click('add-farinha');
    await type(byLabel('Nome da receita'), 'Bolo');
    await click('Salvar receita');
    await flush();
    expect(host.querySelectorAll('[role="alert"]').length).toBe(1);
    await act(async () => {
      (host.querySelector('button[aria-label="Remover Farinha"]') as HTMLElement).click();
    });
    expect(host.querySelectorAll('[role="alert"]').length).toBe(0);
  });

  it('ajuste manual que não é número bloqueia prévia e salvar e marca o campo', async () => {
    previewRecipe.mockResolvedValue(previa());
    await mount();
    await preencher();
    await type(byLabel('Nome da receita'), 'Bolo');
    await click('Calcular');
    await type(host.querySelector('input[aria-label^="Ajustar "]')!, 'abc');
    expect(host.textContent).toContain('Número inválido');
    await click('Calcular');
    await click('Salvar receita');
    expect(previewRecipe).toHaveBeenCalledTimes(1);
    expect(createRecipe).not.toHaveBeenCalled();
  });

  it('ajuste inválido mostra aviso visível e valor não finito também é inválido', async () => {
    previewRecipe.mockResolvedValue(previa());
    await mount();
    await preencher();
    await click('Calcular');
    expect(host.querySelector('[data-testid="ajuste-invalido"]')).toBeNull();
    await type(host.querySelector('input[aria-label^="Ajustar "]')!, '1e999');
    expect(host.querySelector('[data-testid="ajuste-invalido"]')).not.toBeNull();
    await type(host.querySelector('input[aria-label^="Ajustar "]')!, '12,5');
    expect(host.querySelector('[data-testid="ajuste-invalido"]')).toBeNull();
  });

  it('quantidade não finita não é enviada como número válido e modo de preparo vai com trim', async () => {
    previewRecipe.mockResolvedValue(previa());
    await mount();
    await preencher();
    await type(host.querySelector('textarea')!, '  misture  ');
    await type(byLabel('Quantidade'), 'Infinity');
    await click('Calcular');
    expect(previewRecipe).toHaveBeenCalledWith(
      expect.objectContaining({ instructions: 'misture', ingredients: [expect.objectContaining({ quantity: NaN })] }),
    );
  });

  it('prévia bem-sucedida limpa o erro de salvar', async () => {
    createRecipe.mockRejectedValue(new ApiError('falhou ao salvar', 422, { error: 'final_weight_invalid', code: 'final_weight_invalid', message: 'falhou ao salvar' }));
    previewRecipe.mockResolvedValue(previa());
    await mount();
    await preencher();
    await type(byLabel('Nome da receita'), 'Bolo');
    await click('Salvar receita');
    await flush();
    expect(host.textContent).toContain('Não foi possível concluir');
    await click('Calcular');
    await flush();
    expect(host.textContent).not.toContain('Não foi possível concluir');
  });

  it('cancelar com alterações pede confirmação', async () => {
    const onCancel = vi.fn();
    await mount({ onCancel });
    await type(byLabel('Nome da receita'), 'X');
    await click('Cancelar');
    expect(onCancel).not.toHaveBeenCalled();
    await click('Descartar alterações');
    expect(onCancel).toHaveBeenCalled();
  });

  const existente = (status: string) =>
    ({
      id: 3,
      name: 'Bolo',
      recipe: {
        instructions: null,
        final_weight_g: 90,
        effective: null,
        ingredients: [{ position: 0, food_id: 5, portion_id: null, label: 'Farinha', quantity: 100, unit: 'g', catalog_status: status }],
      },
    }) as never;

  it('marca ingrediente desatualizado e indisponível', async () => {
    await mount({ recipe: existente('outdated') });
    expect(host.querySelector('[data-testid="status-ingrediente"]')!.textContent).toContain('mudaram no catálogo');
    await act(async () => root?.unmount());
    host.remove();
    await mount({ recipe: existente('unavailable') });
    expect(host.querySelector('[data-testid="status-ingrediente"]')!.textContent).toContain('mantido com os valores gravados');
    expect(host.textContent).not.toContain('Atualizar ingredientes e recalcular');
  });

  it('atualizar ingredientes pede confirmação e salva com refresh_ingredients', async () => {
    updateRecipe.mockResolvedValue({ id: 3 });
    await mount({ recipe: existente('outdated') });
    await click('Atualizar ingredientes e recalcular');
    expect(host.textContent).toContain('substitui os valores gravados');
    expect(updateRecipe).not.toHaveBeenCalled();
    await click('Confirmar atualização');
    expect(updateRecipe).toHaveBeenCalledWith(3, expect.objectContaining({ refresh_ingredients: true }));
  });

  it('mudar a quantidade zera o aviso da linha', async () => {
    await mount({ recipe: existente('outdated') });
    expect(host.querySelector('[data-testid="status-ingrediente"]')).not.toBeNull();
    await type(byLabel('Quantidade'), '120');
    expect(host.querySelector('[data-testid="status-ingrediente"]')).toBeNull();
  });

  it('com ajuste inválido não abre a confirmação e mostra o aviso de ajuste inválido', async () => {
    previewRecipe.mockResolvedValue(previa());
    await mount({ recipe: existente('outdated') });
    await click('Calcular');
    await type(host.querySelector('input[aria-label^="Ajustar "]')!, '1e999');
    await click('Atualizar ingredientes e recalcular');
    expect(host.textContent).not.toContain('substitui os valores gravados');
    expect(host.querySelector('[data-testid="ajuste-invalido"]')).not.toBeNull();
    expect(updateRecipe).not.toHaveBeenCalled();
  });

  it('falha ao buscar alimento existente mostra erro e não rejeita', async () => {
    fetchFood.mockRejectedValueOnce(new Error('rede'));
    await mount();
    await click('abrir-cadastro');
    await click('usar-existente');
    await flush();
    expect(host.querySelector('[data-testid="erro-existente"]')).not.toBeNull();
    await click('usar-existente');
    await flush();
    expect(host.querySelector('[data-testid="ingrediente"]')!.textContent).toContain('Leite');
  });

  it('salvar normal não envia refresh_ingredients', async () => {
    updateRecipe.mockResolvedValue({ id: 3 });
    await mount({ recipe: existente('outdated') });
    await click('Salvar receita');
    expect(updateRecipe.mock.calls[0][1]).not.toHaveProperty('refresh_ingredients');
  });

  it('mostra o histórico de versões', async () => {
    fetchRecipeVersions.mockResolvedValue({ items: [{ version: 2, created_at: null, final_weight_g: 90, energy_kcal_per_100g: 250 }] });
    await mount({ recipe: existente('current') });
    await flush();
    expect(host.querySelector('[data-testid="historico-versoes"]')!.textContent).toContain('Versão 2 · peso final 90 g · 250 kcal por 100 g');
  });

  it('cadastra alimento novo sem sair da receita e o adiciona como ingrediente', async () => {
    await mount();
    await click('abrir-cadastro');
    await click('salvar-cadastro');
    expect(host.querySelector('[data-testid="cadastro-alimento"]')).toBeNull();
    expect(host.querySelector('[data-testid="ingrediente"]')!.textContent).toContain('iogurte');
  });

  it('link interno com alterações pede confirmação; remove o listener ao desmontar', async () => {
    const a = document.createElement('a');
    a.setAttribute('href', '/treino');
    document.body.appendChild(a);
    const nav = vi.fn();
    const onDoc = (e: Event) => {
      if (e.target !== a) return;
      nav();
      e.preventDefault();
    };
    document.addEventListener("click", onDoc);
    await mount();
    await act(async () => a.click());
    expect(nav).toHaveBeenCalledTimes(1); // sem alteração: segue
    await type(byLabel('Nome da receita'), 'X');
    nav.mockClear();
    await act(async () => a.click());
    expect(nav).not.toHaveBeenCalled(); // interceptado na captura
    expect(host.textContent).toContain('Sair sem salvar?');
    await click('Continuar editando');
    expect(host.textContent).not.toContain('Sair sem salvar?');
    await act(async () => a.click());
    await click('Sair e descartar');
    expect(nav).toHaveBeenCalledTimes(1); // liberado
    nav.mockClear();
    await act(async () => root?.unmount());
    root = null;
    await act(async () => a.click());
    expect(nav).toHaveBeenCalledTimes(1);
    document.removeEventListener("click", onDoc);
    a.remove();
  });

  it('cancelar sem alterações sai direto', async () => {
    const onCancel = vi.fn();
    await mount({ onCancel });
    await click('Cancelar');
    expect(onCancel).toHaveBeenCalled();
  });
});
