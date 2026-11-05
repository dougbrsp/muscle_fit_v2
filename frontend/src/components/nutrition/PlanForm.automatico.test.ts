// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement, act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import motorConstants from '@/lib/motor-constants.generated.json';
import { PlanForm } from './PlanForm';

// Editor do plano: o modo automático troca "Nível de atividade" por "Rotina fora do exercício" (opções do motor, sem
// o termo "active"), com o default leve; os rótulos dizem se o fator inclui exercício.

const createPlan = vi.hoisted(() => vi.fn());
vi.mock('@/lib/nutrition-api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/nutrition-api')>()),
  createNutritionPlan: createPlan,
  saveNutritionPlan: createPlan,
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

async function montar() {
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
          createElement(PlanForm, { userId: null, plan: null, onSaved: vi.fn(), onCancel: vi.fn() }),
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

describe('PlanForm: modo automático pelo Intervals', () => {
  it('os rótulos do motor dizem se o fator inclui exercício, e o automático existe', () => {
    const rotulosModo = N.tdee_mode_labels as Record<string, string>;
    expect(rotulosModo.explicit_training).toContain('SEM exercício');
    expect(rotulosModo.included_in_factor).toContain('COM exercício');
    expect(rotulosModo[N.auto_tdee_mode]).toContain('Intervals');
    expect(N.default_routine_level).toBe('light');
    expect(Object.values(N.routine_level_labels as Record<string, string>).join(' ')).not.toMatch(/\bactive\b/i);
  });

  it('modos clássicos mostram "Nível de atividade"; o automático troca por "Rotina fora do exercício" com o default leve', async () => {
    await montar();
    expect(rotulos().some((t) => t.startsWith('Nível de atividade'))).toBe(true);
    expect(rotulos().some((t) => t.startsWith('Rotina fora do exercício'))).toBe(false);
    await escolher('Gasto do treino', (N.tdee_mode_labels as Record<string, string>)[N.auto_tdee_mode]);
    expect(rotulos().some((t) => t.startsWith('Rotina fora do exercício'))).toBe(true);
    expect(rotulos().some((t) => t.startsWith('Nível de atividade'))).toBe(false);
    expect(campo('Rotina fora do exercício')!.value).toBe((N.routine_level_labels as Record<string, string>)[N.default_routine_level]);
    expect(host.textContent).toContain('sincronização parada');
  });
});
