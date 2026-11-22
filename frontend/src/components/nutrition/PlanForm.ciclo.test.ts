// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement, act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import motorConstants from '@/lib/motor-constants.generated.json';
import type { NutritionPlan } from '@/lib/nutrition-types';
import { PlanForm, initialState } from './PlanForm';

// A política do ciclo é lembrada ao desligar e religar o ciclo, e vem do plano salvo mesmo sem ciclo.

const N = motorConstants.nutrition;
const [VARY, FAT] = N.carb_cycle_policies;

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
});

function plano(sobre: Record<string, unknown>): NutritionPlan {
  return {
    id: 1,
    strategy: 'cutting',
    protein_g_per_kg: 2,
    fat_kcal_share: 0.25,
    weekly_rate_share: 0.005,
    bmr_formula: 'mifflin',
    tdee_mode: 'explicit_training',
    activity_level: null,
    meals_per_day: 4,
    macro_priority: 'protein_first',
    manual_targets: null,
    cycle_days: null,
    state: 'draft',
    ...sobre,
  } as unknown as NutritionPlan;
}

async function montar(p: NutritionPlan) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  root = createRoot(host);
  await act(async () => {
    root!.render(
      createElement(
        QueryClientProvider,
        { client },
        createElement(
          MantineProvider,
          { env: 'test' },
          createElement(PlanForm, { userId: null, plan: p, onSaved: vi.fn(), onCancel: vi.fn() }),
        ),
      ),
    );
  });
}

const chave = () => {
  const l = Array.from(host.querySelectorAll('label')).find((x) => x.textContent?.startsWith('Dias de mais e menos gasto'));
  return document.getElementById(l!.htmlFor) as HTMLInputElement;
};
const escolhida = () => host.querySelector<HTMLInputElement>('input[type="radio"][name]:checked')?.value;
const opcao = (valor: string) =>
  Array.from(host.querySelectorAll<HTMLInputElement>('input[type="radio"]')).find((i) => i.value === valor)!;
async function alternarCiclo() {
  await act(async () => {
    chave().click();
  });
}

describe('PlanForm: política do ciclo lembrada', () => {
  it('estado inicial de plano salvo sem ciclo guarda a política devolvida pela API', () => {
    const s = initialState(plano({ cycle_days: null, carb_cycle_policy: FAT }));
    expect(s.cycleOn).toBe(false);
    expect(s.cyclePolicy).toBe(FAT);
  });

  it('plano sem o campo ou novo: padrão', () => {
    expect(initialState(plano({})).cyclePolicy).toBe(N.default_carb_cycle_policy);
    expect(initialState(null).cyclePolicy).toBe(N.default_carb_cycle_policy);
  });

  it('abrir plano sem ciclo com a política guardada e ligar o ciclo mostra a política guardada', async () => {
    await montar(plano({ cycle_days: null, carb_cycle_policy: FAT }));
    expect(host.querySelector('input[type="radio"]')).toBeNull();
    await alternarCiclo();
    expect(escolhida()).toBe(FAT);
  });

  it('desligar e religar o ciclo mantém a política escolhida', async () => {
    await montar(plano({ cycle_days: null, carb_cycle_policy: VARY }));
    await alternarCiclo();
    await act(async () => {
      opcao(FAT).click();
    });
    expect(escolhida()).toBe(FAT);
    await alternarCiclo();
    expect(host.querySelector('input[type="radio"]')).toBeNull();
    await alternarCiclo();
    expect(escolhida()).toBe(FAT);
  });
});
