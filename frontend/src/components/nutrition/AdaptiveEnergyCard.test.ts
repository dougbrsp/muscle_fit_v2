// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { createElement, act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import type { AdaptiveEnergy, AdaptiveEnergyState } from '@/lib/nutrition-dashboard';
import { adaptiveLabel } from '@/lib/nutrition-dashboard';
import motorConstants from '@/lib/motor-constants.generated.json';
import { AdaptiveEnergyCard } from './AdaptiveEnergyCard';

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

async function montar(energy: AdaptiveEnergy | null | undefined) {
  root = createRoot(host);
  await act(async () => {
    root!.render(createElement(MantineProvider, { env: 'test' }, createElement(AdaptiveEnergyCard, { energy })));
  });
}

const texto = () => (host.textContent ?? '').replace(/ /g, ' ');

const ESTADO: AdaptiveEnergyState = {
  base_tdee: 2500,
  activity_calories_today: 300,
  average_activity_calories: 250,
  activity_adjustment: 50,
  estimated_tdee_today: 2550,
  confidence_level: 'medium',
  valid_days: 12,
  window_days: 21,
  last_update_date: '2026-11-04',
  trend_weight_kg: 80.4,
  weight_slope_kg_per_week: -0.35,
  suspension_reasons: [],
  data_quality_flags: [],
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

describe('AdaptiveEnergyCard', () => {
  it('bloco ausente: não aparece', async () => {
    await montar(undefined);
    expect(host.querySelector('[data-testid="adaptativo-cartao"], [data-testid="adaptativo-indisponivel"]')).toBeNull();
    expect(host.textContent).not.toContain('Gasto adaptativo');
  });

  it('indisponível: só o aviso curto e o selo de sombra', async () => {
    await montar({ available: false, applied: false, unavailable_reason: 'no_data', unavailable_reason_label: 'Ainda sem dados.', state: null, days: [] });
    expect(host.querySelector('[data-testid="adaptativo-indisponivel"]')).not.toBeNull();
    expect(host.querySelector('[data-testid="adaptativo-selo"]')?.textContent).toContain('ainda não altera suas metas');
    expect(host.querySelector('[data-testid="adaptativo-cartao"]')).toBeNull();
  });

  it('completo: valores prontos do payload, confiança com dias válidos', async () => {
    await montar({ available: true, applied: false, state: ESTADO, days: [{ day: '2026-11-04', confirmed: true, activities: [ATIVIDADE] }] });
    const t = texto();
    expect(t).toContain('2.500 kcal');
    expect(t).toContain('2.550 kcal');
    expect(host.querySelector('[data-testid="adaptativo-confianca"]')?.textContent).toBe('Média (12 de 21 dias válidos)');
    expect(t).toContain('-0,35 kg/semana');
    expect(host.querySelector('[data-testid="adaptativo-falta"]')).toBeNull();
    expect(host.querySelector('[data-testid="adaptativo-atividade"]')).toBeNull();
    const botao = host.querySelector('[data-testid="adaptativo-atividades-toggle"]') as HTMLElement;
    expect(botao.getAttribute('aria-expanded')).toBe('false');
    expect(document.getElementById(botao.getAttribute('aria-controls') ?? '')?.hasAttribute('hidden')).toBe(true);
    await act(async () => {
      botao.click();
    });
    expect(botao.getAttribute('aria-expanded')).toBe('true');
    expect(document.getElementById(botao.getAttribute('aria-controls') ?? '')?.hasAttribute('hidden')).toBe(false);
    const a = texto();
    expect(a).toContain('Corrida leve');
    expect(a).toContain('45 min');
    expect(a).toContain('Informada: 500 kcal');
    expect(a).toContain('Ativa normalizada: 420 kcal');
    expect(a).toContain(adaptiveLabel('day_sync_status', 'confirmed'));
  });

  it('com pendências: lista o que falta com have/need', async () => {
    await montar({
      available: true,
      applied: false,
      state: { ...ESTADO, suspension_reasons: [
          { code: 'weighins_below_min', have: 3, need: 7 },
          { code: 'weight_slope_implausible', have: null, need: null },
        ], data_quality_flags: ['bmr_missing'] },
      days: [],
    });
    const falta = host.querySelector('[data-testid="adaptativo-falta"]')?.textContent ?? '';
    expect(falta).toContain('Poucas pesagens por semana. (3 de 7)');
    // Sem `have`/`need` numéricos: só o rótulo, sem "(- de -)".
    expect(falta).toContain('A variação de peso na janela é grande demais para ser confiável.');
    expect(falta).not.toContain('(- de -)');
    expect(falta).not.toMatch(/confiável\. \(/);
    expect(falta).toContain('Sem taxa metabólica basal calculável.');
  });

  it('possível duplicata e fora do total, com dia pendente de sincronização', async () => {
    await montar({
      available: true,
      applied: false,
      state: ESTADO,
      days: [
        { day: '2026-11-04', confirmed: false, activities: [{ ...ATIVIDADE, possible_duplicate_of: [2], counts_in_total: false }] },
      ],
    });
    await act(async () => {
      (host.querySelector('[data-testid="adaptativo-atividades-toggle"]') as HTMLElement).click();
    });
    expect(host.querySelector('[data-testid="adaptativo-duplicata"]')).not.toBeNull();
    expect(host.querySelector('[data-testid="adaptativo-fora-total"]')).not.toBeNull();
    expect(host.querySelector('[data-testid="adaptativo-dia-pendente"]')?.textContent).toContain(adaptiveLabel('day_sync_status', 'pending'));
  });

  it('em uso no plano (applied=true): o selo deixa de dizer "em sombra"', async () => {
    await montar({ available: true, applied: true, state: ESTADO, days: [] });
    const selo = host.querySelector('[data-testid="adaptativo-selo"]')?.textContent ?? '';
    expect(selo).toBe('Em uso no seu plano');
    expect(selo).not.toContain('sombra');
  });

  it('rótulos conhecidos vêm do JSON gerado, nunca do código cru', () => {
    expect(adaptiveLabel('confidence_level', 'high')).not.toBe('high');
    expect(adaptiveLabel('suspension_reason', 'complete_days_below_min')).not.toBe('complete_days_below_min');
    expect(adaptiveLabel('unavailable_reason', 'no_data')).not.toBe('no_data');
    // Todo código do JSON resolve para o texto do próprio JSON.
    const blocos = (motorConstants.nutrition as unknown as { adaptive_energy_labels: Record<string, Record<string, string>> }).adaptive_energy_labels;
    expect(Object.keys(blocos).sort()).toEqual(
      ['activity_flag', 'calorie_mode', 'confidence_level', 'data_quality_flag', 'day_confirmation_source', 'day_sync_status', 'suspension_reason', 'unavailable_reason'],
    );
    for (const [grupo, codigos] of Object.entries(blocos)) {
      for (const [codigo, texto] of Object.entries(codigos)) {
        expect(texto.length).toBeGreaterThan(0);
        expect(adaptiveLabel(grupo, codigo)).toBe(texto);
        expect(texto).not.toBe(codigo);
      }
    }
  });
});
