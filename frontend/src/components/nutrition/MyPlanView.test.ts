// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement, act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { NutritionMyPlan } from '@/lib/nutrition-types';

const fetchMock = vi.fn();
vi.mock('@/lib/nutrition-api', async (orig) => ({
  ...(await orig<typeof import('@/lib/nutrition-api')>()),
  fetchMyNutritionPlan: () => fetchMock(),
}));

import { MyPlanView } from './MyPlanView';

let root: Root | null = null;
let host: HTMLElement;

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  (globalThis as { ResizeObserver?: unknown }).ResizeObserver ||= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
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
  fetchMock.mockReset();
});

async function montar(dados: Partial<NutritionMyPlan>) {
  fetchMock.mockResolvedValue({
    plan: { id: 1, state: 'current', tdee_mode: 'adaptive_balance', effective_from: '2026-11-01', energy_params: null, energy_model_version: 'v1' },
    today: '2026-11-05',
    subject: { body_fat_percent: 20, weight_kg: 80 },
    goal: { goal_body_fat_percent: null, target_weight_for_goal_kg: null, goal_weight_kg: null },
    today_target: null,
    week: [],
    missing: [],
    ...dados,
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  root = createRoot(host);
  await act(async () => {
    root!.render(createElement(QueryClientProvider, { client: qc }, createElement(MantineProvider, { env: 'test' }, createElement(MyPlanView))));
  });
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

describe('MyPlanView: confiança do gasto', () => {
  it('sem energy: não mostra a confiança', async () => {
    await montar({});
    expect(host.textContent).toContain('Seu plano');
    expect(host.textContent).not.toContain('Confiança do gasto');
  });

  it('com energy: mostra a confiança pronta do payload', async () => {
    await montar({ energy: { confidence_level: 'medium', valid_days: 12, window_days: 21 } });
    expect(host.textContent).toContain('Confiança do gasto');
    expect(host.textContent).toContain('Média (12 de 21 dias válidos)');
  });
});
