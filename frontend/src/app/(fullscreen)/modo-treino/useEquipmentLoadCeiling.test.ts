// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement, act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

const apiFetch = vi.fn();
const notify = vi.fn();
vi.mock('@/lib/api', () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
  isTransientApiError: (error: unknown) => (error as { transient?: boolean })?.transient === true,
}));
vi.mock('@mantine/notifications', () => ({ notifications: { show: (...args: unknown[]) => notify(...args) } }));

import { PAIR_SUGGESTION_QUERY_ROOT, WORKOUT_MODE_PLAN_QUERY_ROOT } from '@/lib/deload';
import { EQUIPMENT_LOAD_CEILING_QUERY_ROOT } from '@/lib/equipment-load-ceiling';
import { useEquipmentLoadCeiling, type LoadCeilingControl } from './useEquipmentLoadCeiling';

let root: Root | null = null;
let host: HTMLElement;
let client: QueryClient;
let control: LoadCeilingControl;

const PAIR = { mappingId: 5, gymEquipmentId: 9 };

function Probe({ pair }: { pair: typeof PAIR | null }) {
  control = useEquipmentLoadCeiling(pair);
  return null;
}

async function mount(pair: typeof PAIR | null = PAIR) {
  root = createRoot(host);
  await act(async () => {
    root!.render(createElement(QueryClientProvider, { client }, createElement(Probe, { pair })));
  });
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0)); // deixa a leitura inicial assentar
  });
}

// Espera deterministica: cede o laco de eventos dentro de `act` ate o estado refletir.
async function until(predicate: () => boolean, tries = 100) {
  for (let i = 0; i < tries && !predicate(); i += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 5));
    });
  }
  expect(predicate()).toBe(true);
}

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});
beforeEach(() => {
  apiFetch.mockReset();
  notify.mockReset();
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  host = document.createElement('div');
  document.body.appendChild(host);
});
afterEach(async () => {
  await act(async () => {
    root?.unmount();
  });
  root = null;
  host.remove();
});

describe('useEquipmentLoadCeiling', () => {
  it('lê o teto do par e expõe o valor do servidor', async () => {
    apiFetch.mockResolvedValueOnce({ ok: true, max_load_kg: 100 });
    await mount();
    expect(apiFetch).toHaveBeenCalledWith('/me/equipment-load-ceiling?mapping_id=5&gym_equipment_id=9');
    await until(() => control.maxLoadKg === 100);
    expect(control.maxLoadKg).toBe(100);
  });

  it('sem par: não consulta e não marca', async () => {
    await mount(null);
    expect(apiFetch).not.toHaveBeenCalled();
    await act(async () => control.onMark(50));
    expect(apiFetch).not.toHaveBeenCalled();
  });

  it('marcar: PUT com o par e a carga, atualiza o cache e invalida teto, sugestão e ficha', async () => {
    apiFetch.mockResolvedValueOnce({ ok: true, max_load_kg: null }); // leitura inicial
    await mount();
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    apiFetch.mockResolvedValueOnce({ ok: true, max_load_kg: 80 });
    apiFetch.mockResolvedValue({ ok: true, max_load_kg: 80 }); // refetch pós-invalidação
    await act(async () => control.onMark(80));
    await until(() => control.maxLoadKg === 80);
    const put = apiFetch.mock.calls.find((call) => call[1]?.method === 'PUT');
    expect(put?.[0]).toBe('/me/equipment-load-ceiling');
    expect(JSON.parse(put?.[1].body)).toEqual({ mapping_id: 5, gym_equipment_id: 9, max_load_kg: 80 });
    expect(control.maxLoadKg).toBe(80);
    const roots = invalidate.mock.calls.map((call) => (call[0] as { queryKey: string[] }).queryKey[0]);
    expect(roots).toEqual(
      expect.arrayContaining([EQUIPMENT_LOAD_CEILING_QUERY_ROOT, PAIR_SUGGESTION_QUERY_ROOT, WORKOUT_MODE_PLAN_QUERY_ROOT]),
    );
  });

  it('desfazer: DELETE do par e teto volta a null', async () => {
    apiFetch.mockResolvedValueOnce({ ok: true, max_load_kg: 80 });
    await mount();
    apiFetch.mockResolvedValue({ ok: true, max_load_kg: null });
    await act(async () => control.onClear());
    await until(() => control.maxLoadKg === null);
    const del = apiFetch.mock.calls.find((call) => call[1]?.method === 'DELETE');
    expect(del?.[0]).toBe('/me/equipment-load-ceiling?mapping_id=5&gym_equipment_id=9');
    expect(control.maxLoadKg).toBeNull();
  });

  it('offline: avisa de forma amigável e não perde o teto atual', async () => {
    apiFetch.mockResolvedValueOnce({ ok: true, max_load_kg: null });
    await mount();
    apiFetch.mockRejectedValue({ transient: true });
    await act(async () => control.onMark(80));
    await until(() => notify.mock.calls.length === 1);
    expect(notify).toHaveBeenCalledTimes(1);
    expect(String(notify.mock.calls[0][0].message)).toContain('Sem conexão');
    expect(control.maxLoadKg).toBeNull();
  });
});
