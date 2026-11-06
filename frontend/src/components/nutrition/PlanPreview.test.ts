// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { createElement, act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { NutritionTargetDetail } from '@/lib/nutrition-types';
import { planPreviewQuery } from '@/lib/nutrition-api';
import { TodayTargetCard } from './PlanPreview';

// Composição do gasto no card "Meta de hoje": a tela só exibe as parcelas prontas do motor.

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

function alvo(sobre: Record<string, unknown>): NutritionTargetDetail {
  return {
    kcal: 2400,
    kcal_delta: -300,
    protein_g: 160,
    carbs_g: 250,
    fat_g: 70,
    fiber_g: 30,
    protein_per_meal_g: 40,
    training_known: true,
    bmr: { kcal: 1700, formula: 'mifflin', requested_formula: 'mifflin', ffm_kg: null },
    scale_comparison: null,
    training: { kcal: 400, kcal_nature: 'reported', nature: null, quality: null },
    kcal_target: { kcal: 2400, delta_kcal: -300, rate_applied: null, clamped_by: [] },
    tdee: { kcal: 2700, mode: 'explicit_training', activity_factor: 1.4, bmr_kcal: 1700, training_kcal: 400 },
    ...sobre,
  } as unknown as NutritionTargetDetail;
}

async function montar(target: NutritionTargetDetail) {
  root = createRoot(host);
  await act(async () => {
    root!.render(createElement(MantineProvider, { env: 'test' }, createElement(TodayTargetCard, { target })));
  });
}

async function abrir() {
  await act(async () => {
    (host.querySelector('[data-testid="gasto-composicao-toggle"]') as HTMLElement).click();
  });
  return (host.querySelector('[data-testid="gasto-composicao"]')?.textContent ?? '').replace(/ /g, ' ');
}

const parte = (code: string, label: string, kcal: number) => ({ code, label, kcal });

describe('TodayTargetCard: composição do gasto (só renderiza `tdee_breakdown`)', () => {
  it('explícito: renderiza as parcelas do motor e o total', async () => {
    await montar(
      alvo({ tdee_breakdown: { parts: [parte('base', 'Gasto-base de rotina (TMB × fator sem exercício)', 2300), parte('training', 'Treino de força', 400)], notes: [], total_kcal: 2700 } }),
    );
    const texto = await abrir();
    expect(texto).toContain('Gasto-base de rotina (TMB × fator sem exercício)2.300 kcal');
    expect(texto).toContain('Treino de força400 kcal');
    expect(texto).toContain('Gasto total2.700 kcal');
    expect(host.querySelector('[data-testid="gasto-nota"]')).toBeNull();
  });

  it('embutido no fator: só a base e a observação do motor', async () => {
    await montar(
      alvo({
        tdee: { kcal: 2300, mode: 'included_in_factor', activity_factor: 1.55, bmr_kcal: 1700, training_kcal: null },
        tdee_breakdown: {
          parts: [parte('base', 'Gasto total pelo fator com exercício (TMB × fator, treino já incluso)', 2300)],
          notes: [{ code: 'training_included_in_factor', label: 'O fator de atividade já inclui o seu exercício; o treino do dia não é somado de novo.' }],
          total_kcal: 2300,
        },
      }),
    );
    const texto = await abrir();
    expect(host.querySelector('[data-testid="gasto-nota"]')?.textContent).toBe('O fator de atividade já inclui o seu exercício; o treino do dia não é somado de novo.');
    expect(texto).not.toContain('Treino de força400');
    expect(texto).toContain('Gasto total2.300 kcal');
  });

  it('automático sincronizado com extras: três parcelas', async () => {
    await montar(
      alvo({
        tdee_breakdown: {
          parts: [parte('base', 'Gasto-base de rotina (TMB × fator sem exercício)', 2492), parte('training', 'Treino de força', 428), parte('extras', 'Cardio e caminhadas do Intervals', 320)],
          notes: [],
          total_kcal: 3240,
        },
      }),
    );
    const texto = await abrir();
    expect(texto).toContain('Cardio e caminhadas do Intervals320 kcal');
    expect(texto).toContain('Treino de força428 kcal');
    expect(texto).toContain('Gasto total3.240 kcal');
  });

  it('automático em fallback: base e aviso do motor', async () => {
    await montar(
      alvo({
        tdee_breakdown: {
          parts: [parte('base', 'Estimado pelo fator com exercício (Intervals sem sincronizar)', 3071)],
          notes: [{ code: 'auto_fallback', label: 'Estimado pelo fator: Intervals sem sincronizar. O treino e o cardio deste dia não foram somados ao gasto.' }],
          total_kcal: 3071,
        },
      }),
    );
    const texto = await abrir();
    expect(texto).toContain('Intervals sem sincronizar');
    expect(host.querySelector('[data-testid="gasto-nota"]')?.textContent).toContain('não foram somados');
    expect(texto).toContain('Gasto total3.071 kcal');
  });

  it('sem tdee_breakdown (backend antigo ou null): sem botão, linha atual mantida', async () => {
    await montar(alvo({}));
    expect(host.querySelector('[data-testid="gasto-composicao"]')).toBeNull();
    expect(host.textContent).toContain('Gasto total');
    expect(host.textContent).toContain('2.700');
  });

  it('o botão informa o estado e o painel que controla (acessibilidade)', async () => {
    await montar(alvo({ tdee_breakdown: { parts: [parte('base', 'Base', 2700)], notes: [], total_kcal: 2700 } }));
    const botao = host.querySelector('[data-testid="gasto-composicao-toggle"]') as HTMLElement;
    expect(botao.getAttribute('aria-expanded')).toBe('false');
    const painel = botao.getAttribute('aria-controls') ?? '';
    expect(painel).not.toBe('');
    await act(async () => {
      botao.click();
    });
    expect(botao.getAttribute('aria-expanded')).toBe('true');
    expect(document.getElementById(painel)?.hasAttribute('hidden')).toBe(false);
  });

  it('adaptativo: parcela de ajuste negativa mantém o sinal e a confiança/última atualização/pendências aparecem', async () => {
    await montar(
      alvo({
        tdee_breakdown: {
          parts: [parte('base', 'Gasto adaptativo médio', 2400), parte('activity_adjustment', 'Ajuste pela atividade', -120)],
          notes: [],
          total_kcal: 2280,
        },
        energy: {
          confidence_level: 'medium',
          valid_days: 12,
          window_days: 21,
          last_update_date: '2026-11-04',
          suspension_reasons: [{ code: 'weighins_below_min', have: 3, need: 7 }],
          data_quality_flags: [],
        },
      }),
    );
    const texto = await abrir();
    expect(texto).toMatch(/Ajuste pela atividade-120 kcal/);
    expect(texto).toContain('Gasto total2.280 kcal');
    expect(host.querySelector('[data-testid="adaptativo-confianca"]')?.textContent).toBe('Média (12 de 21 dias válidos)');
    expect(host.textContent).toContain('04/11/2026');
    expect(host.querySelector('[data-testid="adaptativo-falta"]')?.textContent).toContain('(3 de 7)');
  });

  it('legado: sem `energy`, nada de confiança', async () => {
    await montar(alvo({}));
    expect(host.querySelector('[data-testid="adaptativo-confianca"]')).toBeNull();
  });

  it('o card Plano e a prévia compartilham a mesma queryKey (um só POST)', () => {
    const plano = { id: 7, updated_at: '2026-11-05T10:00:00', state: 'draft' };
    expect(planPreviewQuery(plano).queryKey).toEqual(planPreviewQuery({ ...plano }).queryKey);
    expect(planPreviewQuery(plano).queryKey).not.toEqual(planPreviewQuery({ ...plano, updated_at: '2026-11-05T11:00:00' }).queryKey);
  });
});
