// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { createElement, act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { NutritionDayEnergyDetail } from '@/lib/nutrition-types';
import { adaptiveLabel } from '@/lib/nutrition-dashboard';
import { DayEnergySection } from './DayEnergySection';

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

async function montar(detail: NutritionDayEnergyDetail | null | undefined) {
  root = createRoot(host);
  await act(async () => {
    root!.render(createElement(MantineProvider, { env: 'test' }, createElement(DayEnergySection, { detail })));
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
});
