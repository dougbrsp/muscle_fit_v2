// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { createElement, act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import type { FoodDetail } from '@/lib/nutrition-types';
import { FoodDetailCard } from './FoodDetailCard';

vi.mock('./PortionsEditor', () => ({ PortionsEditor: () => null }));

let root: Root | null = null;
let host: HTMLElement | null = null;

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  window.matchMedia ||= ((query: string) => ({
    matches: false, media: query, onchange: null, addListener() {}, removeListener() {},
    addEventListener() {}, removeEventListener() {}, dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
  class RO { observe() {} unobserve() {} disconnect() {} }
  (globalThis as { ResizeObserver?: unknown }).ResizeObserver ||= RO;
});

afterEach(async () => {
  await act(async () => { root?.unmount(); });
  root = null;
  host?.remove();
  document.body.innerHTML = '';
});

const alimento = (permissions: Record<string, boolean>) =>
  ({
    id: 7, name: 'Barra de whey', barcode: '7891234567895', basis: 'g', source: 'user', is_public: false, is_own: true,
    per_100: {}, serving: { serving_size: null, household_measure: null, servings_per_package: null },
    field_sources: [], permissions: { can_edit: true, can_add_portion: true, can_promote: false, ...permissions },
  }) as unknown as FoodDetail;

async function montar(food: FoodDetail, onDelete?: () => Promise<unknown>, deleting = false) {
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root!.render(
      createElement(
        MantineProvider,
        { env: 'test' },
        createElement(FoodDetailCard, { food, onEdit: vi.fn(), onPromote: vi.fn(), promoting: false, onPortionAdded: vi.fn(), onDelete, deleting }),
      ),
    );
  });
}
const botao = (texto: string) =>
  Array.from(document.querySelectorAll('button')).find((b) => b.textContent?.trim() === texto) as HTMLButtonElement | undefined;
const clicar = async (texto: string) => {
  await act(async () => { botao(texto)!.click(); });
  await act(async () => { await new Promise((r) => setTimeout(r, 25)); });
};

describe('FoodDetailCard: excluir alimento (#140)', () => {
  it('sem can_delete (ausente) o botão não existe', async () => {
    await montar(alimento({}), vi.fn());
    expect(botao('Excluir alimento')).toBeUndefined();
  });

  it('can_delete falso: o botão não existe', async () => {
    await montar(alimento({ can_delete: false }), vi.fn());
    expect(botao('Excluir alimento')).toBeUndefined();
  });

  it('com can_delete: pede confirmação com nome e código de barras; cancelar não chama a API', async () => {
    const onDelete = vi.fn(async () => ({}));
    await montar(alimento({ can_delete: true }), onDelete);
    await clicar('Excluir alimento');
    expect(document.body.textContent).toContain('Barra de whey');
    expect(document.body.textContent).toContain('7891234567895');
    await clicar('Cancelar');
    expect(onDelete).not.toHaveBeenCalled();
  });

  it('confirmar chama a exclusão uma vez; erro da API não derruba a tela', async () => {
    const onDelete = vi.fn(async () => {
      throw new Error('food_forbidden');
    });
    await montar(alimento({ can_delete: true }), onDelete);
    await clicar('Excluir alimento');
    await clicar('Excluir');
    expect(onDelete).toHaveBeenCalledTimes(1);
    expect(document.body.textContent).toContain('Barra de whey');
  });

  it('em curso: o botão de confirmar fica desabilitado', async () => {
    await montar(alimento({ can_delete: true }), vi.fn(), true);
    await clicar('Excluir alimento');
    const confirmar = Array.from(document.querySelectorAll('button')).filter((b) => b.textContent?.includes('Excluir')).pop()!;
    expect(confirmar.disabled || confirmar.hasAttribute('data-disabled')).toBe(true);
  });
});
