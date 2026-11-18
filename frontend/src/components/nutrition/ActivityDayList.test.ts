// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement, act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { adaptiveLabel, type AdaptiveActivity } from '@/lib/nutrition-dashboard';
import * as api from '@/lib/nutrition-api';
import { ActivityDayList } from './ActivityDayList';

vi.mock('@/lib/nutrition-api', async (orig) => {
  const real = await orig<typeof import('@/lib/nutrition-api')>();
  return {
    ...real,
    putActivityCorrection: vi.fn().mockResolvedValue({}),
    deleteActivityCorrection: vi.fn().mockResolvedValue({}),
  };
});

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
});

beforeEach(() => {
  vi.clearAllMocks();
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
});

async function montar(activities: AdaptiveActivity[]) {
  root = createRoot(host);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root!.render(
      createElement(
        QueryClientProvider,
        { client },
        createElement(MantineProvider, { env: 'test' }, createElement(ActivityDayList, { day: { day: '2026-11-04', confirmed: true, activities }, editable: true })),
      ),
    );
  });
}

const sel = (id: string) => host.querySelector(`[data-testid="${id}"]`) as HTMLElement | null;

const BASE: AdaptiveActivity = {
  id: 1,
  start: '2026-11-04T07:00:00',
  duration_minutes: 45,
  name: 'Corrida leve',
  reported_kcal: 500,
  calorie_mode: 'active',
  active_kcal: 420,
  counts_in_total: true,
  flags: [],
  possible_duplicate_of: [],
  source: 'app',
  correction: null,
};

describe('ActivityDayList', () => {
  it('automática (sem correção): selo Automático e rótulo do modo vindo do JSON', async () => {
    await montar([BASE]);
    expect(sel('adaptativo-tipo-origem')?.textContent).toBe('Automático');
    expect(host.textContent).toContain(adaptiveLabel('calorie_mode', 'active'));
    expect(sel('atividade-desfazer')).toBeNull();
    expect(sel('adaptativo-origem')).toBeNull();
  });

  it('manual (reclassify): selo Manual e Desfazer chama o DELETE uma vez', async () => {
    await montar([{ ...BASE, correction: { action: 'reclassify', energy_kind: 'gross' } }]);
    expect(sel('adaptativo-tipo-origem')?.textContent).toBe('Manual');
    expect(host.textContent).not.toContain('Automático');
    await act(async () => {
      sel('atividade-desfazer')!.click();
    });
    expect(api.deleteActivityCorrection).toHaveBeenCalledTimes(1);
    expect(api.deleteActivityCorrection).toHaveBeenCalledWith(1);
  });

  it('origem Intervals mostra o selo; sessão do app não', async () => {
    await montar([{ ...BASE, source: 'intervals_icu' }, { ...BASE, id: 2, source: 'app' }]);
    expect(host.querySelectorAll('[data-testid="adaptativo-origem"]').length).toBe(1);
    expect(host.querySelector('[data-testid="adaptativo-origem"]')?.textContent).toBe('Intervals');
  });

  it('excluída: riscada, sem selo Automático/Manual e com Desfazer', async () => {
    await montar([{ ...BASE, excluded: true, counts_in_total: false, correction: { action: 'exclude' } }]);
    expect(sel('adaptativo-atividade')?.getAttribute('data-excluida')).toBe('true');
    expect(sel('adaptativo-tipo-origem')).toBeNull();
    expect(sel('adaptativo-fora-total')).toBeNull();
    expect(sel('atividade-desfazer')).not.toBeNull();
  });
});
