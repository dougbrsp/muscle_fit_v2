// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, createElement as h } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import {
  CARB_CYCLE_POLICY_OPTIONS,
  DAY_KIND_OPTIONS,
  DEFAULT_CARB_CYCLE_POLICY,
  DEFAULT_DAY_KIND,
  WEEKDAY_OPTIONS,
} from '@/lib/nutrition-constants';
import type { NutritionDayTarget } from '@/lib/nutrition-types';
import { cyclePolicyPayload } from '@/lib/nutrition-cycle-policy';
import { flatCycleDays, PlanCycleDays } from './PlanCycleDays';

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

afterEach(async () => {
  await act(async () => {
    root?.unmount();
  });
  root = null;
  host?.remove();
  document.body.innerHTML = '';
});

async function montar(enabled: boolean, onPolicyChange = vi.fn(), week?: NutritionDayTarget[]) {
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
  const client = new QueryClient();
  await act(async () => {
    root!.render(
      h(
        QueryClientProvider,
        { client },
        h(
          MantineProvider,
          { env: 'test' },
          h(PlanCycleDays, {
            enabled,
            onEnabledChange: vi.fn(),
            value: flatCycleDays(DEFAULT_DAY_KIND),
            onChange: vi.fn(),
            policy: DEFAULT_CARB_CYCLE_POLICY,
            onPolicyChange,
            week,
          }),
        ),
      ),
    );
  });
  return onPolicyChange;
}

describe('PlanCycleDays', () => {
  it('ciclo ligado: um seletor por dia da semana e o seletor da política, com as opções do espelho', async () => {
    await montar(true);
    for (const dia of WEEKDAY_OPTIONS) {
      const grupo = host.querySelector(`[aria-label="${dia.label}"]`);
      expect(grupo).not.toBeNull();
      for (const tipo of DAY_KIND_OPTIONS) expect(grupo!.textContent).toContain(tipo.label);
    }
    const politica = host.querySelector('[aria-labelledby="carb-cycle-policy-label"]');
    expect(politica).not.toBeNull();
    for (const o of CARB_CYCLE_POLICY_OPTIONS) expect(politica!.textContent).toContain(o.label);
  });

  it('o seletor da política usa o tamanho padrão (não o xs, pequeno para toque)', async () => {
    await montar(true);
    const politica = host.querySelector('[aria-labelledby="carb-cycle-policy-label"]') as HTMLElement;
    expect(politica.getAttribute('data-size')).not.toBe('xs');
    expect(politica.className).not.toMatch(/size-xs/);
  });

  it('escolher outra política avisa o formulário', async () => {
    const onPolicyChange = await montar(true);
    const outra = CARB_CYCLE_POLICY_OPTIONS.find((o) => o.value !== DEFAULT_CARB_CYCLE_POLICY)!;
    const radio = host.querySelector(
      `[aria-labelledby="carb-cycle-policy-label"] input[value="${outra.value}"]`,
    ) as HTMLInputElement;
    await act(async () => {
      radio.click();
    });
    expect(onPolicyChange).toHaveBeenCalledWith(outra.value);
  });

  it('mostra gordura em g e % de cada dia (da API) e o aviso de carbo limitado com o rótulo pronto', async () => {
    const dia = (local_date: string, fat_g: number, share: number, limitations: object[]) =>
      ({ local_date, fat_g, limitations, macro_kcal_shares: { protein: 0.3, carb: 0.4, fat: share } }) as unknown as NutritionDayTarget;
    await montar(true, vi.fn(), [
      dia('2026-11-23', 70, 0.25, []), // segunda
      dia('2026-11-24', 90, 0.3, [
        { code: 'fat_compensation_carb_limited', label: 'Carbo limitado pelo piso.' },
        { code: 'fat_compensation_cycle_skipped', label: 'Ciclo não aplicado neste dia.' },
      ]), // terça
    ]);
    const linhas = Array.from(host.querySelectorAll('[data-testid="dia-gordura"]')).map((e) => e.textContent);
    expect(linhas).toEqual(['Gordura 70 g (25% das kcal)', 'Gordura 90 g (30% das kcal)']);
    expect(Array.from(host.querySelectorAll('[data-testid="dia-aviso-ciclo"]')).map((e) => e.textContent)).toEqual([
      'Carbo limitado pelo piso.',
      'Ciclo não aplicado neste dia.',
    ]);
  });

  it('sem prévia não mostra gordura por dia', async () => {
    await montar(true);
    expect(host.querySelector('[data-testid="dia-gordura"]')).toBeNull();
  });

  it('ciclo desligado: sem seletores na tela, mas a política segue sendo enviada', async () => {
    await montar(false);
    expect(host.querySelector('[aria-labelledby="carb-cycle-policy-label"]')).toBeNull();
    const outra = CARB_CYCLE_POLICY_OPTIONS.find((o) => o.value !== DEFAULT_CARB_CYCLE_POLICY)!;
    expect(cyclePolicyPayload(outra.value)).toBe(outra.value);
    expect(cyclePolicyPayload(DEFAULT_CARB_CYCLE_POLICY)).toBe(DEFAULT_CARB_CYCLE_POLICY);
  });
});
