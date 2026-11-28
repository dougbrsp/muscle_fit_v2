// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { createElement, act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { NutritionTargetDetail } from '@/lib/nutrition-types';
import { planComparisonQuery, planPreviewQuery } from '@/lib/nutrition-api';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { PlanPreview, TodayTargetCard } from './PlanPreview';

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
  (globalThis as { ResizeObserver?: unknown }).ResizeObserver ||= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
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

  it('ciclo com compensação em gordura: dia baixo mostra a gordura maior que a API mandou, sem recalcular', async () => {
    // Dados de exemplo da resposta da API (dia baixo em carbo com política fat_compensation).
    await montar(alvo({ carbs_g: 120, fat_g: 95, day_kind: 'low', carb_cycle_policy: 'fat_compensation' }));
    const texto = (host.textContent ?? '').replace(/ /g, ' ');
    expect(texto).toMatch(/Gordura\s*95 g/);
    expect(texto).toMatch(/Carboidrato\s*120 g/);
    expect(texto).toMatch(/Alvo calórico\s*2\.400 kcal/);
  });

  it('prévia: gordura em g e %, aviso de carbo limitado e erros/avisos do ciclo com o rótulo da API', async () => {
    const plano = { id: 9, user_id: 1, updated_at: 't', state: 'current' } as never;
    const dia = (limitations: object[]) => ({
      local_date: '2026-11-23', fat_g: 90, kcal: 2000, limitations, macro_kcal_shares: { protein: 0.3, carb: 0.4, fat: 0.3 },
    });
    const previa = {
      plan: plano, today: '2026-11-23', publish_days: [], limitations: [], blocking: [], can_publish: true, manual_check: null,
      subject: {}, goal: {}, projection: { status: 'x', reason_items: [] },
      target: alvo({}),
      warnings: [{ code: 'fat_compensation_carb_limited', label: 'Aviso pronto: carbo limitado.' }],
      week: [dia([{ code: 'fat_compensation_carb_limited', label: 'Carbo do dia limitado.' }])],
    };
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    client.setQueryData(planPreviewQuery(plano).queryKey, previa);
    root = createRoot(host);
    await act(async () => {
      root!.render(
        createElement(
          QueryClientProvider,
          { client },
          createElement(MantineProvider, { env: 'test' }, createElement(PlanPreview, { plan: plano, currentPlan: { id: 9, updated_at: 't' }, onPublished: () => {} })),
        ),
      );
    });
    expect(host.querySelector('[data-testid="gordura-por-dia"] [data-testid="dia-gordura"]')?.textContent).toBe('Gordura 90 g (30% das kcal)');
    expect(host.querySelector('[data-testid="dia-aviso-ciclo"]')?.textContent).toBe('Carbo do dia limitado.');
    // Sem duplicar: o aviso por dia fica só no resumo do dia, não na lista global.
    expect(host.textContent).not.toContain('Aviso pronto: carbo limitado.');
    expect(host.textContent).toContain('Carbo do dia limitado.');
  });

  it('rascunho: aviso de dia fora de publish_days continua na lista geral; o de dia exibido não duplica', async () => {
    const plano = { id: 9, user_id: 1, updated_at: 't', state: 'draft' } as never;
    const dia = (local_date: string, limitations: object[]) => ({
      local_date, fat_g: 90, kcal: 2000, limitations, macro_kcal_shares: { protein: 0.3, carb: 0.4, fat: 0.3 },
    });
    const limitado = { code: 'fat_compensation_carb_limited', label: 'Carbo limitado.' };
    const ignorado = { code: 'fat_compensation_ignored_manual', label: 'Manual ignora compensação.' };
    const previa = {
      plan: plano, today: '2026-11-24', publish_days: ['2026-11-24'], limitations: [], blocking: [], can_publish: true, manual_check: null,
      subject: {}, goal: {}, projection: { status: 'x', reason_items: [] }, target: alvo({}),
      warnings: [limitado, ignorado],
      week: [dia('2026-11-23', [ignorado]), dia('2026-11-24', [limitado])],
    };
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    client.setQueryData(planPreviewQuery(plano).queryKey, previa);
    root = createRoot(host);
    await act(async () => {
      root!.render(
        createElement(
          QueryClientProvider,
          { client },
          createElement(MantineProvider, { env: 'test' }, createElement(PlanPreview, { plan: plano, currentPlan: { id: 1, updated_at: 't' }, onPublished: () => {} })),
        ),
      );
    });
    const texto = host.textContent ?? '';
    expect(texto.match(/Carbo limitado\./g)).toHaveLength(1); // só no dia exibido
    expect(texto).toContain('Manual ignora compensação.'); // dia 23 fora de publish_days: fica na lista geral
  });

  it('comparação com o vigente: só no rascunho com vigente diferente, antes do botão Publicar', async () => {
    const vigente = { id: 3, user_id: 1, updated_at: 'v1', state: 'current' };
    const montarCom = async (plano: never, currentPlan: { id: number; updated_at: string | null } | null, comComparacao: boolean) => {
      const previa = {
        plan: plano, today: '2026-11-24', publish_days: ['2026-11-24'], limitations: [], blocking: [], can_publish: true, manual_check: null,
        subject: {}, goal: {}, projection: { status: 'x', reason_items: [] }, target: alvo({}), warnings: [], week: [],
      };
      const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
      client.setQueryData(planPreviewQuery(plano).queryKey, previa);
      if (comComparacao) {
        client.setQueryData(planComparisonQuery(plano, vigente).queryKey, { status: 'no_current', reasons: [], today: '2026-11-24', current: null, draft: null, comparison: null });
      }
      root = createRoot(host);
      await act(async () => {
        root!.render(
          createElement(
            QueryClientProvider,
            { client },
            createElement(MantineProvider, { env: 'test' }, createElement(PlanPreview, { plan: plano, currentPlan, onPublished: () => {} })),
          ),
        );
      });
    };
    const rascunho = { id: 9, user_id: 1, updated_at: 't', state: 'draft' } as never;
    await montarCom(rascunho, { id: 3, updated_at: 'v1' }, true);
    const aviso = host.querySelector('[data-testid="comparacao-sem-vigente"]');
    expect(aviso).not.toBeNull();
    const publicar = [...host.querySelectorAll('button')].find((b) => b.textContent === 'Publicar plano')!;
    expect(aviso!.compareDocumentPosition(publicar) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    await act(async () => root?.unmount());
    host.innerHTML = '';
    await montarCom({ ...vigente } as never, { id: 3, updated_at: 'v1' }, false);
    expect(host.textContent).not.toMatch(/Comparando|vigente para comparar|Comparação com o plano vigente/);
  });

  it('o card Plano e a prévia compartilham a mesma queryKey (um só POST)', () => {
    const plano = { id: 7, updated_at: '2026-11-05T10:00:00', state: 'draft' };
    expect(planPreviewQuery(plano).queryKey).toEqual(planPreviewQuery({ ...plano }).queryKey);
    expect(planPreviewQuery(plano).queryKey).not.toEqual(planPreviewQuery({ ...plano, updated_at: '2026-11-05T11:00:00' }).queryKey);
  });
});
