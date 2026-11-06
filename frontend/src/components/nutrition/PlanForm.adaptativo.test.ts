// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement, act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import motorConstants from '@/lib/motor-constants.generated.json';
import type { NutritionPlan } from '@/lib/nutrition-types';
import { PlanForm } from './PlanForm';

// Editor do plano no 4º modo (adaptive_balance): campo "Calorias do relógio", dicas de estimativa inicial e `energy_params`
// só no payload do adaptativo; planos legados enviam o mesmo corpo de antes.

const createPlan = vi.hoisted(() => vi.fn());
vi.mock('@/lib/nutrition-api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/nutrition-api')>()),
  createNutritionPlan: createPlan,
  updateNutritionPlan: createPlan,
}));

const N = motorConstants.nutrition;

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

const esperar = async (vezes = 3) => {
  for (let i = 0; i < vezes; i++) await act(async () => { await new Promise((r) => setTimeout(r, 25)); });
};

async function montar(plan: NutritionPlan | null = null) {
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
          createElement(PlanForm, { userId: null, plan, onSaved: vi.fn(), onCancel: vi.fn() }),
        ),
      ),
    );
  });
  await esperar();
}

const rotulos = () => Array.from(host.querySelectorAll('label')).map((l) => l.textContent ?? '');
const campo = (texto: string) => {
  const l = Array.from(host.querySelectorAll('label')).find((x) => x.textContent?.startsWith(texto));
  return l ? (document.getElementById(l.htmlFor) as HTMLInputElement | null) : null;
};
async function escolher(rotulo: string, opcao: string) {
  const entrada = campo(rotulo)!;
  await act(async () => { entrada.click(); });
  const item = Array.from(document.querySelectorAll('[role="option"]')).find((o) => o.textContent === opcao) as HTMLElement | undefined;
  expect(item, opcao).toBeDefined();
  await act(async () => { item!.click(); });
  await esperar();
}


function plano(sobre: Record<string, unknown>): NutritionPlan {
  return {
    id: 7,
    user_id: 1,
    status: 'draft',
    state: 'draft',
    notes: null,
    strategy: 'cutting',
    advanced: false,
    protein_g_per_kg: 2,
    protein_basis: 'lean',
    fat_kcal_share: 0.25,
    weekly_rate_share: 0.005,
    surplus_share: null,
    carb_min_g_per_kg: null,
    bmr_formula: 'mifflin',
    tdee_mode: 'explicit_training',
    activity_level: null,
    meals_per_day: 4,
    goal_body_fat_percent: null,
    goal_weight_kg: null,
    carb_cycling_enabled: false,
    macro_priority: 'protein_first',
    manual_targets: null,
    cycle_days: null,
    ...sobre,
  } as unknown as NutritionPlan;
}

async function salvar(): Promise<Record<string, unknown>> {
  createPlan.mockResolvedValue({});
  const botao = Array.from(host.querySelectorAll('button')).find((b) => b.textContent === 'Salvar rascunho')!;
  await act(async () => {
    botao.click();
  });
  await esperar();
  return createPlan.mock.calls[0][1] as Record<string, unknown>;
}

describe('PlanForm: modo adaptativo', () => {
  it('plano legado: abre com o mesmo corpo de antes, sem `energy_params` e sem o campo do relógio', async () => {
    await montar(plano({}));
    expect(rotulos().some((t) => t.startsWith('Calorias do relógio'))).toBe(false);
    const corpo = await salvar();
    expect('energy_params' in corpo).toBe(false);
    expect(corpo.tdee_mode).toBe('explicit_training');
  });

  it('adaptativo: mostra o campo "Calorias do relógio", as dicas de estimativa inicial e envia `energy_params`', async () => {
    await montar(plano({ tdee_mode: 'adaptive_balance', energy_params: { watch_calorie_mode: 'gross' } }));
    expect(rotulos().some((t) => t.startsWith('Calorias do relógio'))).toBe(true);
    expect(rotulos().some((t) => t.startsWith('Fórmula da TMB'))).toBe(true);
    expect(rotulos().some((t) => t.startsWith('Nível de atividade'))).toBe(true);
    const dicas = N.adaptive_field_hints as Record<string, string>;
    // Toda dica que o dono exporta tem de aparecer (falha se uma chave nova, ex.: watch_calorie_mode, não for exibida).
    for (const [campo, texto] of Object.entries(dicas)) expect(host.textContent, campo).toContain(texto);
    const rotulosKcal = N.adaptive_energy_labels.calorie_mode as Record<string, string>;
    expect(campo('Calorias do relógio')!.value).toBe(rotulosKcal.gross);
    const corpo = await salvar();
    expect(corpo.energy_params).toEqual({ watch_calorie_mode: 'gross' });
    expect(corpo.tdee_mode).toBe('adaptive_balance');
  });

  it('adaptativo sem energy_params salvo: o relógio começa em "não informado" (unknown)', async () => {
    await montar(plano({ tdee_mode: 'adaptive_balance' }));
    const corpo = await salvar();
    expect(corpo.energy_params).toEqual({ watch_calorie_mode: 'unknown' });
  });

  it('adaptativo salvo com `energy_params: null` (padrão do motor): abre com o padrão e reenvia o padrão', async () => {
    await montar(plano({ tdee_mode: 'adaptive_balance', energy_params: null }));
    const rotulosKcal = N.watch_calorie_mode_labels as Record<string, string>;
    expect(campo('Calorias do relógio')!.value).toBe(rotulosKcal[N.default_watch_calorie_mode]);
    const corpo = await salvar();
    expect(corpo.energy_params).toEqual({ watch_calorie_mode: N.default_watch_calorie_mode });
  });

  it('voltar para um modo legado remove `energy_params` do corpo', async () => {
    await montar(plano({ tdee_mode: 'adaptive_balance', energy_params: { watch_calorie_mode: 'active' } }));
    await escolher('Gasto do treino', (N.tdee_mode_labels as Record<string, string>).explicit_training);
    expect(rotulos().some((t) => t.startsWith('Calorias do relógio'))).toBe(false);
    const corpo = await salvar();
    expect('energy_params' in corpo).toBe(false);
    expect(corpo.tdee_mode).toBe('explicit_training');
  });

  it('selecionar o adaptativo a partir de um plano legado: mostra o campo e envia energy_params com o padrão do motor', async () => {
    await montar(plano({}));
    expect(rotulos().some((t) => t.startsWith('Calorias do relógio'))).toBe(false);
    await escolher('Gasto do treino', (N.tdee_mode_labels as Record<string, string>)[N.adaptive_tdee_mode]);
    expect(rotulos().some((t) => t.startsWith('Calorias do relógio'))).toBe(true);
    const corpo = await salvar();
    expect(corpo.tdee_mode).toBe(N.adaptive_tdee_mode);
    expect(corpo.energy_params).toEqual({ watch_calorie_mode: N.default_watch_calorie_mode });
  });

  it('as opções do relógio e o modo adaptativo vêm do JSON gerado', () => {
    expect(N.tdee_modes).toContain(N.adaptive_tdee_mode);
    expect(N.manual_training_modes_by_tdee_mode[N.adaptive_tdee_mode as 'adaptive_balance']).toEqual(['fixed']);
    expect(N.watch_calorie_modes).toContain(N.default_watch_calorie_mode);
  });
});
