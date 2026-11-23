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

async function montar(enabled: boolean, onPolicyChange = vi.fn()) {
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

  it('ciclo desligado: sem seletores na tela, mas a política segue sendo enviada', async () => {
    await montar(false);
    expect(host.querySelector('[aria-labelledby="carb-cycle-policy-label"]')).toBeNull();
    const outra = CARB_CYCLE_POLICY_OPTIONS.find((o) => o.value !== DEFAULT_CARB_CYCLE_POLICY)!;
    expect(cyclePolicyPayload(outra.value)).toBe(outra.value);
    expect(cyclePolicyPayload(DEFAULT_CARB_CYCLE_POLICY)).toBe(DEFAULT_CARB_CYCLE_POLICY);
  });
});
