// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { createElement, act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import motorConstants from '@/lib/motor-constants.generated.json';
import type { NutritionPlan } from '@/lib/nutrition-types';
import type { AdaptiveEnergyState } from '@/lib/nutrition-dashboard';
import { PlanSummary } from './PlanSummary';

// Card "Plano": no modo adaptativo mostra o modo da kcal do relógio e a versão do modelo; nos legados nada muda.

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
    protein_basis: 'lean',
    fat_kcal_share: 0.25,
    weekly_rate_share: 0.005,
    bmr_formula: 'mifflin',
    tdee_mode: 'explicit_training',
    activity_level: null,
    meals_per_day: 4,
    goal_weight_kg: null,
    macro_priority: 'protein_first',
    manual_targets: null,
    cycle_days: null,
    state: 'current',
    ...sobre,
  } as unknown as NutritionPlan;
}

async function montar(p: NutritionPlan, energy: AdaptiveEnergyState | null = null) {
  root = createRoot(host);
  await act(async () => {
    root!.render(createElement(MantineProvider, { env: 'test' }, createElement(PlanSummary, { plan: p, energy })));
  });
}

describe('PlanSummary: modelo energético', () => {
  it('legado: sem as linhas do adaptativo', async () => {
    await montar(plano({}));
    expect(host.textContent).not.toContain('Calorias do relógio');
    expect(host.textContent).not.toContain('Modelo energético');
  });

  it('adaptativo publicado: rótulo do modo da kcal e versão do modelo', async () => {
    await montar(plano({ tdee_mode: 'adaptive_balance', energy_params: { watch_calorie_mode: 'active' }, energy_model_version: 'adaptive-v1' }));
    const rotulos = motorConstants.nutrition.adaptive_energy_labels.calorie_mode as Record<string, string>;
    expect(host.textContent).toContain(`Calorias do relógio${rotulos.active}`);
    expect(host.textContent).toContain('Modelo energéticoadaptive-v1');
  });

  it('adaptativo em rascunho: versão ainda não gravada', async () => {
    await montar(plano({ tdee_mode: 'adaptive_balance', energy_model_version: null }));
    expect(host.textContent).toContain('Definido ao publicar');
  });

  it('adaptativo com `energy`: mostra a confiança e os dias válidos', async () => {
    await montar(plano({ tdee_mode: 'adaptive_balance' }), { confidence_level: 'high', valid_days: 18, window_days: 21 });
    expect(host.textContent).toContain('Confiança do gastoAlta (18 de 21 dias válidos)');
  });

  it('legado com `energy` informado por engano: não mostra confiança', async () => {
    await montar(plano({}), { confidence_level: 'high', valid_days: 18, window_days: 21 });
    expect(host.textContent).not.toContain('Confiança do gasto');
  });
});
