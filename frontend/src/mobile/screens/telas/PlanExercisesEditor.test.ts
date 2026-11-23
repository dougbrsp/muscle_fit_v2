// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { theme } from '@/theme';
import type { WorkoutPlanExercise } from '@/lib/workout-plans-types';
import { PlanExercisesEditor } from './PlanExercisesEditor';

vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  apiFetch: vi.fn(async () => ({ items: [] })),
}));

let root: Root | null = null;
let host: HTMLElement;

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  window.matchMedia ??= ((q: string) => ({
    matches: false,
    media: q,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    onchange: null,
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
});

afterEach(async () => {
  await act(async () => {
    root?.unmount();
  });
  root = null;
  host?.remove();
});

// Dados de exemplo de resposta da API (a tela só exibe).
const row = (over: Partial<WorkoutPlanExercise>): WorkoutPlanExercise => ({
  sequence: 1,
  exercise_name: 'Supino reto',
  muscle_primary: 'Peito',
  sets_count: 3,
  reps_target: '8-10',
  warmup: false,
  ...over,
});

async function render(exercises: WorkoutPlanExercise[], readOnly = false) {
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root!.render(
      createElement(
        QueryClientProvider,
        { client: new QueryClient({ defaultOptions: { queries: { retry: false } } }) },
        createElement(
          MantineProvider,
          { theme },
          createElement(PlanExercisesEditor, { exercises, onChange: () => {}, readOnly }),
        ),
      ),
    );
  });
  // Toca na primeira linha da lista para abrir a edição de página inteira.
  const rowEl = Array.from(host.querySelectorAll<HTMLElement>('*')).find(
    (el) => el.children.length === 0 && el.textContent?.startsWith('1. '),
  );
  await act(async () => {
    rowEl?.click();
  });
}

const rpeInputs = () =>
  Array.from(host.querySelectorAll<HTMLInputElement>('input[aria-label^="RPE esperado da série"]'));

describe('PlanExercisesEditor: RPE por série', () => {
  it('mostra um campo por série com os alvos vindos da ficha', async () => {
    await render([row({ rpe_targets_by_set: [8, 9, 10], rpe_edited: false })]);
    expect(host.textContent).toContain('RPE esperado por série');
    expect(rpeInputs().map((i) => i.value)).toEqual(['8', '9', '10']);
  });

  it('linha só com texto gravado mostra o alvo literal, sem campos por série', async () => {
    await render([row({ rpe_target: '8-9', rpe_targets_by_set: [], rpe_edited: false })]);
    expect(host.textContent).toContain('Alvo gravado: 8-9');
    expect(rpeInputs()).toHaveLength(0);
  });

  it('linha sem alvos e sem texto mostra os campos em branco, um por série', async () => {
    await render([row({ rpe_targets_by_set: [], rpe_edited: false })]);
    expect(rpeInputs().map((i) => i.value)).toEqual(['', '', '']);
  });

  it('somente leitura usa o campo de texto, sem campos por série', async () => {
    await render([row({ rpe_targets_by_set: [8, 9, 10] })], true);
    expect(rpeInputs()).toHaveLength(0);
  });
});
