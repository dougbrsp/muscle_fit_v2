// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { theme } from '@/theme';
import { EquipmentSubstituteModal } from './EquipmentSubstituteModal';

const apiFetch = vi.fn();
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  apiFetch: (...args: unknown[]) => apiFetch(...args),
}));

let root: Root | null = null;
let host: HTMLElement;

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  window.matchMedia ??= ((q: string) => ({
    matches: false,
    media: q,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
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
  apiFetch.mockReset();
});

afterEach(async () => {
  await act(async () => {
    root?.unmount();
  });
  root = null;
  host?.remove();
});

const gymItem = (over: Record<string, unknown>) => ({
  id: 1,
  mapping_id: 12,
  equipment_id: 30,
  equipment_name: 'Cabo',
  mapping_exercise_name: 'Crucifixo',
  photo_url: null,
  load_convention: 'total',
  ...over,
});

const recommendBody = (convention: string) => ({
  ok: true,
  has_equipment_for_group: true,
  alternatives: [],
  excluded: [],
  recommended: gymItem({ id: 2, equipment_id: 31, equipment_name: 'Peck deck', load_convention: convention }),
});

const catalogPair = {
  id: 9,
  equipment_id: 40,
  equipment_name: 'Crossover',
  exercise_id: 12,
  exercise_name: 'Crucifixo',
  is_active: true,
  load_convention: 'total',
};

type Route = Record<string, (url: string, init?: RequestInit) => unknown>;

function routeApi(routes: Route) {
  apiFetch.mockImplementation(async (url: string, init?: RequestInit) => {
    const key = Object.keys(routes).find((prefix) => url.startsWith(prefix));
    if (!key) return { items: [] };
    return routes[key](url, init);
  });
}

const baseRoutes = (recommendConvention = 'per_side'): Route => ({
  '/gym-equipment/recommend': () => recommendBody(recommendConvention),
  '/gym-equipment?': () => ({ items: [gymItem({})], can_add_to_gym: true }),
  '/equipment-exercises': () => ({ items: [catalogPair] }),
});

type Props = Parameters<typeof EquipmentSubstituteModal>[0];

function tree(onSelect: Props['onSelect'], opened: boolean) {
  return createElement(
    QueryClientProvider,
    { client: client! },
    createElement(
      MantineProvider,
      { theme },
      createElement(EquipmentSubstituteModal, {
        opened,
        onClose: () => {},
        gymId: 1,
        mappingId: 12,
        primaryGroup: 'Peito',
        currentEquipmentLinkId: 1,
        onSelect,
      }),
    ),
  );
}

let client: QueryClient | null = null;

async function mount(opened = true) {
  const onSelect = vi.fn();
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
  await settle(() => root!.render(tree(onSelect, opened)));
  await settle(() => {}); // segunda rodada: as consultas terminam depois da primeira
  return { onSelect, rerender: (open: boolean) => settle(() => root!.render(tree(onSelect, open))) };
}

const settle = (fn: () => void) =>
  act(async () => {
    fn();
    await new Promise((r) => setTimeout(r, 200));
  });

const button = (label: string) =>
  Array.from(document.body.querySelectorAll('button')).find((b) => b.textContent?.includes(label)) as
    | HTMLButtonElement
    | undefined;

const click = (el: HTMLElement) => settle(() => el.dispatchEvent(new MouseEvent('click', { bubbles: true })));
const byTestId = (id: string) => document.body.querySelector(`[data-testid="${id}"]`);

describe('EquipmentSubstituteModal: recomendado fora da lista (#244)', () => {
  it('convenção diferente da atual: mostra o aviso e só escolhe após Confirmar', async () => {
    routeApi(baseRoutes('per_side'));
    const { onSelect } = await mount();
    
    await click(button('Sugerir substituto')!);
    await click(button('Peck deck')!);
    expect(byTestId('equipment-convention-warning')).not.toBeNull();
    expect(onSelect).not.toHaveBeenCalled();
    await click(button('Confirmar')!);
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ equipment_link_id: 2, equipment_name: 'Peck deck' }));
  });

  it('mesma convenção: escolhe direto, sem aviso', async () => {
    routeApi(baseRoutes('total'));
    const { onSelect } = await mount();
    await click(button('Sugerir substituto')!);
    await click(button('Peck deck')!);
    expect(byTestId('equipment-convention-warning')).toBeNull();
    expect(onSelect).toHaveBeenCalledTimes(1);
  });
});

describe('EquipmentSubstituteModal: acréscimo ao catálogo (#246)', () => {
  const addRoutes = (refetch: () => unknown): Route => {
    let listCalls = 0;
    return {
      ...baseRoutes(),
      '/gym-equipment?': () => {
        listCalls += 1;
        return listCalls === 1 ? { items: [gymItem({})], can_add_to_gym: true } : refetch();
      },
      '/gym-equipment-catalog': () => ({ status: 'ok', links: [{ id: 55, mapping_id: 12, equipment_id: 40 }] }),
    };
  };
  const added = () => ({
    items: [gymItem({}), gymItem({ id: 55, equipment_id: 40, equipment_name: 'Crossover' })],
    can_add_to_gym: true,
  });

  async function addCrossover() {
    await settle(() => {});
    await click(button('Acrescentar à academia')!);
    await click(button('Confirmar')!);
  }

  it('acréscimo e recarga ok: escolhe o item novo', async () => {
    routeApi(addRoutes(added));
    const { onSelect } = await mount();
    await addCrossover();
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ equipment_link_id: 55 }));
  });

  it('fechar durante o acréscimo não dispara a escolha', async () => {
    let release: (value: unknown) => void = () => {};
    routeApi({
      ...addRoutes(added),
      '/gym-equipment-catalog': () =>
        new Promise((resolve) => {
          release = resolve;
        }),
    });
    const { onSelect, rerender } = await mount();
    await addCrossover();
    await rerender(false);
    await act(async () => {
      release({ status: 'ok', links: [{ id: 55, mapping_id: 12, equipment_id: 40 }] });
      await new Promise((r) => setTimeout(r, 200));
    });
    expect(onSelect).not.toHaveBeenCalled();
  });

  it('recarga falha após o acréscimo: mensagem própria, sem escolher, catálogo continua visível', async () => {
    routeApi(
      addRoutes(() => {
        throw new Error('rede');
      }),
    );
    const { onSelect } = await mount();
    await addCrossover();
    expect(onSelect).not.toHaveBeenCalled();
    expect(byTestId('equipment-catalog-add-message')?.textContent).toContain('não foi possível atualizar a lista');
    expect(byTestId('equipment-section-catalog')).not.toBeNull();
  });
});
