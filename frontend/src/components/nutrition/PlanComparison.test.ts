// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { planComparisonQuery } from '@/lib/nutrition-api';
import type { NutritionPlanComparison } from '@/lib/nutrition-types';
import { PlanComparison, reasonText } from './PlanComparison';

// Rascunho × vigente (#344): a tela só exibe valores, diferenças e % da API.

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  apiFetch: vi.fn(() => Promise.reject(new Error('rede'))),
}));

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

const plano = { id: 9, updated_at: '2026-11-27T10:00:00' };
const vigente = { id: 3, updated_at: '2026-11-01T08:00:00' };
const ctx = (day_kind: string, is_training_day = false) => ({ day_kind, is_training_day, carb_cycle_policy: 'fat_compensation' });
// Diferenças propositalmente "erradas" (não batem com rascunho − vigente): provam que a tela não recalcula.
const v = (current: number, draft: number, diff: number, diff_percent: number | null) => ({ current, draft, diff, diff_percent });
const dia = (local_date: string, differs: boolean) => ({
  local_date,
  values: { kcal: v(2400, 2200, -111, -7.7), protein_g: v(160, 170, 9, 5.5), carbs_g: v(250, 200, -50, -20), fat_g: v(70, 70, 0, 0) },
  current_context: ctx('normal', true),
  draft_context: ctx(differs ? 'low' : 'normal', true),
  differs,
});

function resposta(sobre: Partial<NutritionPlanComparison> = {}): NutritionPlanComparison {
  return {
    status: 'ok',
    reasons: [],
    today: '2026-11-27',
    current: null,
    draft: null,
    comparison: {
      today: '2026-11-27',
      target: dia('2026-11-27', true),
      days: [dia('2026-11-27', true), dia('2026-11-28', false)],
      only_current: [],
      only_draft: [],
    },
    ...sobre,
  };
}

async function montar(data?: NutritionPlanComparison) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  if (data) client.setQueryData(planComparisonQuery(plano, vigente).queryKey, data);
  root = createRoot(host);
  await act(async () => {
    root!.render(
      createElement(
        QueryClientProvider,
        { client },
        createElement(MantineProvider, { env: 'test' }, createElement(PlanComparison, { plan: plano, current: vigente })),
      ),
    );
  });
}

const texto = (sel: string) => (host.querySelector(sel)?.textContent ?? '').replace(/ /g, ' ');

describe('PlanComparison', () => {
  it('meta de hoje: vigente, rascunho e diferença com sinal vindos da API', async () => {
    await montar(resposta());
    const kcal = texto('[data-testid="comparacao-hoje-kcal"]');
    expect(kcal).toContain('2.400 kcal');
    expect(kcal).toContain('2.200 kcal');
    expect(kcal).toContain('-111 kcal (-7,7%)');
    expect(texto('[data-testid="comparacao-hoje-protein_g"]')).toContain('+9');
    expect(texto('[data-testid="comparacao-hoje-protein_g"]')).toContain('(+5,5%)');
  });

  it('semana: dia com `differs` destacado e rotulado, tipos de dia de cada lado', async () => {
    await montar(resposta());
    const linhas = host.querySelectorAll('[data-testid="comparacao-dia"]');
    expect(linhas).toHaveLength(2);
    expect(linhas[0].getAttribute('data-differs')).toBe('true');
    expect(linhas[0].textContent).toContain('Muda');
    expect(linhas[1].getAttribute('data-differs')).toBe('false');
    expect(linhas[1].textContent).not.toContain('Muda');
    expect(linhas[0].textContent).toContain('treino');
  });

  it('legenda e rótulos: Vigente e Rascunho nomeados na tabela e no gráfico acessível', async () => {
    await montar(resposta());
    const cabecalho = texto('[data-testid="comparacao-semana"] thead');
    expect(cabecalho).toContain('Tipo de dia (Vigente)');
    expect(cabecalho).toContain('Tipo de dia (Rascunho)');
    const grafico = host.querySelector('[role="img"]');
    expect(grafico?.getAttribute('aria-label')).toMatch(/Vigente e Rascunho/);
  });

  it('no_current: aviso discreto, sem bloco', async () => {
    await montar(resposta({ status: 'no_current', comparison: null }));
    expect(texto('[data-testid="comparacao-sem-vigente"]')).toBe('Sem plano vigente para comparar');
    expect(host.querySelector('[data-testid="comparacao-plano"]')).toBeNull();
  });

  it('same_plan: não mostra nada', async () => {
    await montar(resposta({ status: 'same_plan', comparison: null }));
    expect(host.querySelector('[data-testid^="comparacao"]')).toBeNull();
    expect(host.textContent).not.toMatch(/vigente|Comparando/i);
  });

  it('partial: motivo legível e o que houver', async () => {
    await montar(resposta({ status: 'partial', reasons: ['current_failed:nutrition_subject_incomplete'], comparison: null }));
    expect(texto('[data-testid="comparacao-parcial"]')).toContain(
      'Não foi possível calcular a prévia do plano vigente (faltam dados do perfil)',
    );
    expect(host.querySelector('[data-testid="comparacao-hoje"]')).toBeNull();
  });

  it('partial com comparação (rascunho bloqueado): aviso e tabelas', async () => {
    await montar(resposta({ status: 'partial', reasons: ['draft_blocking'] }));
    expect(texto('[data-testid="comparacao-parcial"]')).toContain('O rascunho tem bloqueios do motor');
    expect(host.querySelector('[data-testid="comparacao-hoje"]')).not.toBeNull();
  });

  it('erro de rede: aviso', async () => {
    await montar();
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(host.querySelector('[data-testid="comparacao-erro"]')).not.toBeNull();
  });

  it('motivos: código desconhecido volta como está; detalhe desconhecido vira código', () => {
    expect(reasonText('algo_novo')).toBe('algo_novo');
    expect(reasonText('draft_failed:422')).toBe('Não foi possível calcular a prévia do rascunho (código 422)');
  });

  it('a chave muda com a versão do rascunho e com o vigente', () => {
    const k = planComparisonQuery(plano, vigente).queryKey;
    expect(planComparisonQuery({ ...plano, updated_at: 'x' }, vigente).queryKey).not.toEqual(k);
    expect(planComparisonQuery(plano, { ...vigente, updated_at: 'x' }).queryKey).not.toEqual(k);
    expect(planComparisonQuery(plano, { id: 4, updated_at: vigente.updated_at }).queryKey).not.toEqual(k);
  });
});
