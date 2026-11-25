// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement, act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import motorConstants from '@/lib/motor-constants.generated.json';
import type { NutritionPlan } from '@/lib/nutrition-types';
import { planPreviewQuery, updateNutritionPlan } from '@/lib/nutrition-api';
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

vi.mock('@/lib/nutrition-api', async (original) => ({
  ...(await original<typeof import('@/lib/nutrition-api')>()),
  updateNutritionPlan: vi.fn(async () => ({})),
}));

const DIA_GORDURA = { local_date: '2026-11-23', fat_g: 70, limitations: [], macro_kcal_shares: { protein: 0.3, carb: 0.45, fat: 0.25 } };
const PREVIA = { target: { kcal: 2400, macro_kcal_shares: { protein: 0.3, carb: 0.45, fat: 0.25 } }, week: [DIA_GORDURA], warnings: [] };

async function montar(p: NutritionPlan, previa?: unknown) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  if (previa) client.setQueryData(planPreviewQuery(p).queryKey, previa);
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

const temRotulo = (t: string) => Array.from(host.querySelectorAll('label')).some((l) => l.textContent?.startsWith(t));

describe('PlanForm: gramas manuais', () => {
  const manual = { protein_g: 160, carb_g: 250, fat_g: 70, training_mode: 'fixed' };

  it('com gramas manuais esconde proteína g/kg e gordura %, mostra kcal e % da API e mantém o envio dos valores do rascunho', async () => {
    await montar(plano({ manual_targets: manual }), PREVIA);
    expect(temRotulo('Proteína (g/kg)')).toBe(false);
    expect(temRotulo('Gordura (% das kcal)')).toBe(false);
    expect(host.textContent).toContain('30% das kcal');
    expect(host.textContent).toContain('45% das kcal');
    expect(host.textContent).toContain('25% das kcal');
    expect(host.querySelector('[data-testid="manual-energia"]')?.textContent).toContain('2.400');
    const salvar = Array.from(host.querySelectorAll('button')).find((b) => b.textContent === 'Salvar rascunho')!;
    await act(async () => {
      salvar.click();
    });
    const enviado = vi.mocked(updateNutritionPlan).mock.calls[0][1];
    expect(enviado.protein_g_per_kg).toBe(2);
    expect(enviado.fat_kcal_share).toBe(0.25);
    expect(enviado.manual_targets).toMatchObject({ protein_g: 160, carb_g: 250, fat_g: 70 });
  });

  it('formulário editado e não salvo esconde as % e a gordura por dia da prévia salva', async () => {
    await montar(plano({ manual_targets: manual, cycle_days: { '0': 'high' } }), PREVIA);
    const gordura = Array.from(host.querySelectorAll<HTMLInputElement>('input')).find(
      (i) => document.querySelector(`label[for="${i.id}"]`)?.textContent === 'Gordura (g)',
    )!;
    await act(async () => {
      const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
      set.call(gordura, '80');
      gordura.dispatchEvent(new Event('input', { bubbles: true }));
    });
    expect(host.textContent).toContain('Salve para ver as % atualizadas');
    expect(host.textContent).not.toContain('30% das kcal');
    expect(host.querySelector('[data-testid="dia-gordura"]')).toBeNull();
    expect(host.querySelector('[data-testid="manual-energia"]')).toBeNull();
  });

  it('sem gramas manuais os campos da estratégia aparecem', async () => {
    await montar(plano({}));
    expect(temRotulo('Proteína (g/kg)')).toBe(true);
    expect(temRotulo('Gordura (% das kcal)')).toBe(true);
  });

  it('ciclo ligado: gordura em g e % por dia vem da prévia', async () => {
    await montar(plano({ cycle_days: { '0': 'high' } }), PREVIA);
    expect(host.querySelector('[data-testid="dia-gordura"]')?.textContent).toBe('Gordura 70 g (25% das kcal)');
  });
});

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
