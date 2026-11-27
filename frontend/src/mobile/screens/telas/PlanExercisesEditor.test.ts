// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { theme } from '@/theme';
import type { WorkoutPlanExercise } from '@/lib/workout-plans-types';
import { apiFetch } from '@/lib/api';
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

async function render(exercises: WorkoutPlanExercise[], readOnly = false, onChange: (e: WorkoutPlanExercise[]) => void = () => {}) {
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
          createElement(PlanExercisesEditor, { exercises, onChange, readOnly }),
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

const clickEl = async (el: Element | null | undefined) => {
  expect(el).toBeTruthy();
  await act(async () => {
    (el as HTMLElement).click();
  });
};

describe('PlanExercisesEditor: aquecimento e sugestão', () => {
  it('a linha não tem Switch de aquecimento; o warmup vem pela Config. avançada', async () => {
    const onChange = vi.fn();
    await render([row({ warmup: false, warmup_sets_count: 2 })], false, onChange);
    expect(host.querySelector('input[type="checkbox"]')).toBeNull();
    const cfg = Array.from(host.querySelectorAll<HTMLElement>('*')).find(
      (el) => el.children.length === 0 && el.textContent === 'Editar configuração avançada',
    );
    await clickEl(cfg);
    await act(async () => {
      await new Promise((r) => setTimeout(r, 300));
    });
    const label = Array.from(document.body.querySelectorAll('label')).find((l) => l.textContent === 'Fazer aquecimento neste exercício');
    const toggle = document.getElementById(label?.htmlFor ?? '') as HTMLInputElement | null;
    await clickEl(toggle);
    await clickEl(Array.from(document.body.querySelectorAll('button')).find((b) => b.textContent === 'Aplicar'));
    const next = onChange.mock.calls.at(-1)![0] as WorkoutPlanExercise[];
    expect(next[0].warmup).toBe(true);
    expect(next[0].warmup_sets_count).toBe(2);
  });

  it('escolher exercício mantém o RPE por série vindo da sugestão', async () => {
    vi.mocked(apiFetch).mockImplementation(async (path: string) => {
      if (path.startsWith('/mapping-canonical/options')) {
        return { items: [{ id: 9, exercise_name: 'Remada curvada', primary_group: 'Costas' }] } as never;
      }
      if (path.startsWith('/exercises/suggested-progression')) {
        return { reps_target: '8-10', load_target: '', rpe_target: '8', progression_note: null, has_history: false, rpe_targets_by_set: [7, 8, 9] } as never;
      }
      return { items: [] } as never;
    });
    const onChange = vi.fn();
    await render([row({ exercise_name: '', rpe_targets_by_set: [10, 10, 10], rpe_edited: true })], false, onChange);
    const input = host.querySelector<HTMLInputElement>('input[placeholder="Nome do exercício"]')!;
    await act(async () => {
      input.focus();
      input.click();
    });
    await clickEl(Array.from(document.body.querySelectorAll('[role="option"]')).find((o) => o.textContent?.includes('Remada curvada')));
    await act(async () => {});
    const next = onChange.mock.calls.at(-1)![0] as WorkoutPlanExercise[];
    expect(next[0].rpe_targets_by_set).toEqual([7, 8, 9]);
    expect(next[0].rpe_edited).toBe(false);
  });
});
