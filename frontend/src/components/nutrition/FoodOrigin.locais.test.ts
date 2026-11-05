// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement, act, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { MobileNutritionPresentation } from '@/mobile/screens/telas/MobileNutritionPresentation';
import { FavoriteShortcuts } from './FavoriteShortcuts';
import { FoodDetailCard } from './FoodDetailCard';
import { FoodRegistrationFinish } from './FoodRegistrationFinish';
import { FoodSearch } from './FoodSearch';

// O selo de origem (vinda pronta da API) aparece na busca (desktop e celular), nos atalhos de favorito, no detalhe e
// na etapa final; item público de terceiro não diz "Cadastro próprio".

const searchFoods = vi.hoisted(() => vi.fn());
const fetchFavorites = vi.hoisted(() => vi.fn());
vi.mock('@/lib/nutrition-api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/nutrition-api')>()),
  searchFoods,
  fetchFavorites,
}));
vi.mock('next/navigation', () => ({ usePathname: () => '/nutricao', useRouter: () => ({ push: vi.fn() }) }));

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

const alimento = (id: number, origem: { code: string; label: string } | null, extra: Record<string, unknown> = {}) => ({
  id,
  name: `Alimento ${id}`,
  source: 'user',
  basis: 'g',
  origin: origem,
  is_favorite: false,
  is_public: true,
  is_own: false,
  per_100: {},
  serving: { serving_size: null, household_measure: null, servings_per_package: null },
  field_sources: [],
  citation: null,
  brand: null,
  category: null,
  score: 1,
  auto_accept: false,
  permissions: { can_edit: false, can_add_portion: false, can_promote: false },
  portions: [],
  ...extra,
});

async function montar(arvore: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  root = createRoot(host);
  await act(async () => {
    root!.render(createElement(QueryClientProvider, { client }, createElement(MantineProvider, { env: 'test' }, arvore)));
  });
  await esperar(2);
}

async function buscar() {
  const campo = host.querySelector('input') as HTMLInputElement;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(campo, 'alimento');
    campo.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await esperar(20);
}

const RESULTADOS = {
  query: 'alimento',
  normalized: 'alimento',
  items: [
    alimento(1, { code: 'label_photo', label: 'Rótulo (IA)' }),
    alimento(2, { code: 'barcode', label: 'Código de barras' }),
    alimento(3, { code: 'typed', label: 'Digitado' }, { is_public: false, is_own: true }),
  ],
};

describe('selo de origem nos locais da lista', () => {
  beforeEach(() => {
    fetchFavorites.mockResolvedValue({ items: [], max_items: 30 });
    searchFoods.mockResolvedValue(RESULTADOS);
  });

  it('busca (desktop): mostra a origem da API e nunca "Cadastro próprio" em item público de terceiro', async () => {
    await montar(createElement(FoodSearch, { isAdmin: false, selectedId: null, onSelect: vi.fn() }));
    await buscar();
    const texto = host.querySelector('table')?.textContent ?? '';
    expect(texto).toContain('Rótulo (IA)');
    expect(texto).toContain('Código de barras');
    expect(texto).toContain('Digitado');
    expect(texto).not.toContain('Cadastro próprio'); // (o filtro Fonte lista as fontes; a tabela não)
  });

  it('busca (celular): a origem entra na linha do resultado', async () => {
    await montar(
      createElement(
        MobileNutritionPresentation,
        null,
        createElement(FoodSearch, { isAdmin: false, selectedId: null, onSelect: vi.fn() }),
      ),
    );
    await buscar();
    const texto = host.textContent ?? '';
    expect(texto).toContain('Rótulo (IA)');
    expect(texto).toContain('Código de barras');
  });

  it('atalhos de favorito: a origem acompanha o nome', async () => {
    fetchFavorites.mockResolvedValue({
      items: [{ food: alimento(5, { code: 'barcode_off', label: 'Código de barras (Open Food Facts)' }), position: 1, default: null }],
      max_items: 30,
    });
    await montar(createElement(FavoriteShortcuts, { onPick: vi.fn() }));
    await esperar();
    expect(host.textContent).toContain('Alimento 5');
    expect(host.textContent).toContain('Código de barras (Open Food Facts)');
  });

  it('detalhe: mostra a origem no lugar da fonte genérica', async () => {
    await montar(
      createElement(FoodDetailCard, {
        food: alimento(6, { code: 'typed', label: 'Digitado' }, { is_public: false, is_own: true }) as never,
        onEdit: vi.fn(),
        onPromote: vi.fn(),
        promoting: false,
        onPortionAdded: vi.fn(),
      }),
    );
    expect(host.textContent).toContain('Digitado');
    expect(host.textContent).not.toContain('Cadastro próprio');
  });

  it('alimento antigo sem origem na resposta cai no nome da fonte, sem quebrar', async () => {
    searchFoods.mockResolvedValue({ ...RESULTADOS, items: [alimento(7, null)] });
    await montar(createElement(FoodSearch, { isAdmin: false, selectedId: null, onSelect: vi.fn() }));
    await buscar();
    expect(host.textContent).toContain('Alimento 7');
  });
});

describe('etapa final com alimento que já nasce público', () => {
  it('mostra a origem e "Já está no cadastro público", sem o checkbox de público', async () => {
    const food = { id: 8, name: 'Barra', is_public: true, origin: { code: 'label_photo', label: 'Rótulo (IA)' }, permissions: { can_promote: false } };
    await montar(createElement(FoodRegistrationFinish, { food: food as never, onConclude: vi.fn() }));
    expect(host.textContent).toContain('Rótulo (IA)');
    expect(host.textContent).toContain('Já está no cadastro público');
    expect(host.textContent).not.toContain('Adicionar ao cadastro público');
  });
});
