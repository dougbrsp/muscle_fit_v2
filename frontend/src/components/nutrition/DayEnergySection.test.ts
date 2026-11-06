// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement, act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { NutritionDayEnergyDetail } from '@/lib/nutrition-types';
import { adaptiveLabel } from '@/lib/nutrition-dashboard';
import * as api from '@/lib/nutrition-api';
import { DayClosureControl } from './DayClosureControl';
import { DayEnergySection } from './DayEnergySection';

vi.mock('@/lib/nutrition-api', async (orig) => {
  const real = await orig<typeof import('@/lib/nutrition-api')>();
  return {
    ...real,
    putActivityCorrection: vi.fn().mockResolvedValue({}),
    deleteActivityCorrection: vi.fn().mockResolvedValue({}),
    closeNutritionDay: vi.fn().mockResolvedValue({}),
    reopenNutritionDay: vi.fn().mockResolvedValue({}),
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

function Wrap({ children }: { children?: React.ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return createElement(QueryClientProvider, { client }, createElement(MantineProvider, { env: 'test' }, children));
}

async function montar(detail: NutritionDayEnergyDetail | null | undefined) {
  root = createRoot(host);
  await act(async () => {
    root!.render(createElement(Wrap, null, createElement(DayEnergySection, { detail })));
  });
}

async function abrir() {
  const botao = host.querySelector('[data-testid="gasto-do-dia-toggle"]') as HTMLElement;
  await act(async () => {
    botao.click();
  });
  return botao;
}

const texto = () => (host.textContent ?? '').replace(/ /g, ' ');

const BREAKDOWN = {
  parts: [
    { code: 'bmr', label: 'Taxa metabólica basal', kcal: 1700 },
    { code: 'routine', label: 'Rotina fora do exercício', kcal: 500 },
    { code: 'adjust', label: 'Ajuste relativo', kcal: -120 },
  ],
  notes: [{ code: 'n1', label: 'Observação do motor.' }],
  total_kcal: 2080,
};

const ATIVIDADE = {
  id: 1,
  start: '2026-11-04T07:00:00',
  duration_minutes: 45,
  name: 'Corrida leve',
  reported_kcal: 500,
  calorie_mode: 'reported',
  active_kcal: 420,
  counts_in_total: true,
  flags: [],
  possible_duplicate_of: [],
};

describe('DayEnergySection', () => {
  it.each([undefined, null, {}, { breakdown: null, activities: null, energy: null }])('sem detalhe (%j): não renderiza', async (d) => {
    await montar(d as NutritionDayEnergyDetail | null | undefined);
    expect(host.querySelector('[data-testid="gasto-do-dia"]')).toBeNull();
    expect(host.textContent).not.toContain('Gasto do dia');
  });

  it('composição em modo atual: expansível, painel sempre montado e oculto', async () => {
    await montar({ breakdown: BREAKDOWN });
    const botao = host.querySelector('[data-testid="gasto-do-dia-toggle"]') as HTMLElement;
    expect(botao.getAttribute('aria-expanded')).toBe('false');
    const painel = document.getElementById(botao.getAttribute('aria-controls') ?? '');
    expect(painel?.hasAttribute('hidden')).toBe(true);
    expect(texto()).not.toContain('Taxa metabólica basal');
    await abrir();
    expect(botao.getAttribute('aria-expanded')).toBe('true');
    expect(painel?.hasAttribute('hidden')).toBe(false);
    const t = texto();
    expect(t).toContain('Taxa metabólica basal');
    expect(t).toContain('Observação do motor.');
    expect(t).toContain('Gasto total');
    expect(t).toContain('2.080 kcal');
    const ordem = Array.from(host.querySelectorAll('[data-testid="gasto-composicao-linhas"] > div')).map((n) => n.textContent ?? '');
    expect(ordem[0]).toContain('Taxa metabólica basal');
    expect(ordem[1]).toContain('Rotina fora do exercício');
    expect(host.querySelector('[data-testid="gasto-do-dia-energia"]')).toBeNull();
    expect(host.querySelector('[data-testid="gasto-do-dia-atividades"]')).toBeNull();
  });

  it('adaptativo: confiança, pendências e parcela de ajuste negativo', async () => {
    await montar({
      breakdown: BREAKDOWN,
      energy: {
        confidence_level: 'medium',
        valid_days: 12,
        window_days: 21,
        last_update_date: '2026-11-04',
        suspension_reasons: [{ code: 'weighins_below_min', have: 3, need: 7 }],
      },
    });
    await abrir();
    expect(host.querySelector('[data-testid="adaptativo-confianca"]')?.textContent).toBe('Média (12 de 21 dias válidos)');
    expect(host.querySelector('[data-testid="adaptativo-falta"]')?.textContent).toContain('(3 de 7)');
    expect(texto()).toMatch(/Ajuste relativo-120 kcal/);
  });

  it('atividades: duplicata, fora do total e dia pendente', async () => {
    await montar({
      activities: {
        day: '2026-11-04',
        confirmed: false,
        activities: [ATIVIDADE, { ...ATIVIDADE, id: 2, name: 'Corrida duplicada', possible_duplicate_of: [1], counts_in_total: false }],
      },
    });
    await abrir();
    const t = texto();
    expect(t).toContain('Corrida leve');
    expect(t).toContain('45 min');
    expect(t).toContain('Informada: 500 kcal');
    expect(t).toContain('Ativa normalizada: 420 kcal');
    expect(host.querySelectorAll('[data-testid="adaptativo-atividade"]').length).toBe(2);
    expect(host.querySelectorAll('[data-testid="adaptativo-duplicata"]').length).toBe(1);
    expect(host.querySelectorAll('[data-testid="adaptativo-fora-total"]').length).toBe(1);
    expect(host.querySelector('[data-testid="adaptativo-dia-pendente"]')?.textContent).toBe(adaptiveLabel('day_sync_status', 'pending'));
  });

  it('dia confirmado sem atividades', async () => {
    await montar({ activities: { day: '2026-11-04', confirmed: true, activities: [] } });
    await abrir();
    expect(texto()).toContain(adaptiveLabel('day_sync_status', 'confirmed'));
    expect(texto()).toContain('Nenhuma atividade importada.');
  });

  it('sync_status do motor usa o rótulo do JSON; a origem vai no title', async () => {
    await montar({ activities: { day: '2026-11-04', confirmed: true, sync_status: 'confirmed', confirmation_source: 'sync_run', activities: [] } });
    await abrir();
    const selo = host.querySelector('[data-testid="adaptativo-dia-confirmado"]') as HTMLElement;
    expect(selo.textContent).toBe(adaptiveLabel('day_sync_status', 'confirmed'));
    expect(selo.getAttribute('title')).toBe(adaptiveLabel('day_confirmation_source', 'sync_run'));
  });

  it('sync_status pending vence o confirmed legado', async () => {
    await montar({ activities: { day: '2026-11-04', confirmed: true, sync_status: 'pending', activities: [] } });
    await abrir();
    expect(host.querySelector('[data-testid="adaptativo-dia-pendente"]')?.textContent).toBe(adaptiveLabel('day_sync_status', 'pending'));
    expect(host.querySelector('[data-testid="adaptativo-dia-confirmado"]')).toBeNull();
  });

  it('atividade excluída: riscada, selo e Desfazer; as demais oferecem Excluir/Reclassificar', async () => {
    await montar({
      activities: {
        day: '2026-11-04',
        confirmed: true,
        activities: [ATIVIDADE, { ...ATIVIDADE, id: 2, name: 'Corrida duplicada', excluded: true, flags: ['excluded_by_user'], correction: { action: 'exclude' }, counts_in_total: false }],
      },
    });
    await abrir();
    const linhas = host.querySelectorAll('[data-testid="adaptativo-atividade"]');
    expect(linhas[1].getAttribute('data-excluida')).toBe('true');
    expect((linhas[1].querySelector('[data-testid="adaptativo-atividade-nome"]') as HTMLElement).style.textDecoration).toBe('line-through');
    expect(linhas[1].textContent).toContain(adaptiveLabel('activity_flag', 'excluded_by_user'));
    expect(linhas[1].querySelector('[data-testid="atividade-desfazer"]')).not.toBeNull();
    expect(linhas[1].querySelector('[data-testid="atividade-excluir"]')).toBeNull();
    expect(linhas[1].querySelector('[data-testid="adaptativo-fora-total"]')).toBeNull();
    expect(linhas[0].textContent).toContain('Excluir (duplicada)');
    expect(linhas[0].textContent).toContain('Reclassificar');
  });

  it('ações chamam o cliente com os nomes do contrato', async () => {
    await montar({ activities: { day: '2026-11-04', confirmed: true, activities: [ATIVIDADE] } });
    await abrir();
    const clicar = async (id: string) => {
      await act(async () => {
        (host.querySelector(`[data-testid="${id}"]`) as HTMLElement).click();
      });
    };
    await clicar('atividade-excluir');
    expect(api.putActivityCorrection).toHaveBeenCalledWith(1, { action: 'exclude' });
    await clicar('atividade-reclassificar-ativa');
    expect(api.putActivityCorrection).toHaveBeenCalledWith(1, { action: 'reclassify', energy_kind: 'active' });
    await clicar('atividade-reclassificar-total');
    expect(api.putActivityCorrection).toHaveBeenCalledWith(1, { action: 'reclassify', energy_kind: 'gross' });
  });

  it('Desfazer chama o DELETE da correção', async () => {
    await montar({ activities: { day: '2026-11-04', confirmed: true, activities: [{ ...ATIVIDADE, correction: { action: 'reclassify', energy_kind: 'gross' } }] } });
    await abrir();
    await act(async () => {
      (host.querySelector('[data-testid="atividade-desfazer"]') as HTMLElement).click();
    });
    expect(api.deleteActivityCorrection).toHaveBeenCalledWith(1);
    expect(host.querySelector('[data-testid="adaptativo-reclassificada"]')?.textContent).toBe(
      `${adaptiveLabel('activity_correction_action', 'reclassify')}: ${adaptiveLabel('activity_correction_energy_kind', 'gross')}`,
    );
  });
});

describe('DayClosureControl', () => {
  async function montarFechar(props: Partial<Parameters<typeof DayClosureControl>[0]>) {
    root = createRoot(host);
    await act(async () => {
      root!.render(
        createElement(
          Wrap,
          null,
          createElement(DayClosureControl, { date: '2026-11-05', today: '2026-11-06', closure: { closed: false, closed_at: null }, completenessSource: null, ...props }),
        ),
      );
    });
  }
  const botao = () => host.querySelector('[data-testid="fechar-dia-botao"]') as HTMLElement | null;

  it('dia aberto: "Fechei o dia" chama PUT /close', async () => {
    await montarFechar({});
    expect(botao()?.textContent).toBe('Fechei o dia');
    await act(async () => {
      botao()!.click();
    });
    expect(api.closeNutritionDay).toHaveBeenCalledWith('2026-11-05');
  });

  it('dia fechado: "Reabrir o dia" e rótulo "fechado por você"', async () => {
    await montarFechar({ closure: { closed: true, closed_at: '2026-11-05T22:00:00' }, completenessSource: 'manual_close' });
    expect(botao()?.textContent).toBe('Reabrir o dia');
    expect(host.querySelector('[data-testid="fechar-dia-origem"]')?.textContent?.toLowerCase()).toContain('fechado por você');
    await act(async () => {
      botao()!.click();
    });
    expect(api.reopenNutritionDay).toHaveBeenCalledWith('2026-11-05');
  });

  it('completo pela regra; dia futuro sem botão', async () => {
    await montarFechar({ completenessSource: 'intake_rule' });
    expect(host.querySelector('[data-testid="fechar-dia-origem"]')?.textContent?.toLowerCase()).toContain('completo pela regra');
    await act(async () => root?.unmount());
    root = null;
    await montarFechar({ date: '2026-11-07' });
    expect(botao()).toBeNull();
  });
});
