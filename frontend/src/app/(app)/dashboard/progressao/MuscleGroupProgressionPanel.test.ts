// @vitest-environment jsdom
// Issue #319: cada exercício do painel por grupo leva ao detalhe pelo exercise_id.
// (.ts, não .tsx: o vitest.config só inclui src/**/*.test.ts.)
import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { theme } from '@/theme';
import { MuscleGroupProgressionPanel } from './MuscleGroupProgressionPanel';

vi.mock('next/navigation', () => ({
  useRouter: () => ({}),
  usePathname: () => '/dashboard/progressao',
  useSearchParams: () => new URLSearchParams(''),
}));

const EXERCICIOS = [
  { exercise_id: 11, exercise_name: 'Supino', status: 'PROGRESSING' },
  { exercise_id: 12, exercise_name: 'Remada', status: 'STABLE' },
  { exercise_id: 13, exercise_name: 'Agachamento', status: 'PLATEAU_SUSPECTED' },
  { exercise_id: 14, exercise_name: 'Rosca', status: 'REGRESSING' },
  { exercise_id: 0, exercise_name: 'Sem id', status: 'STABLE' },
];

vi.mock('@/lib/dashboard-api', async (orig) => ({
  ...(await orig<typeof import('@/lib/dashboard-api')>()),
  fetchProgressionByMuscleGroup: async () => ({
    current_from: '2026-11-01',
    current_to: '2026-11-26',
    previous_from: '2026-10-01',
    previous_to: '2026-10-31',
    min_sessions_per_window: 2,
    degraded: false,
    items: [
      {
        muscle_group_id: 1,
        muscle_group_name: 'Peito',
        current_tonnage_kg: 0,
        previous_tonnage_kg: 0,
        current_sets: 0,
        previous_sets: 0,
        performance_status: 'PROGRESSING',
        performance_change_pct: null,
        performance_exercises_excluded: 0,
        performance_exercises: EXERCICIOS,
      },
    ],
  }),
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

describe('MuscleGroupProgressionPanel: links dos exercícios', () => {
  it('um link por status com o href do exercise_id; id inválido fica texto', async () => {
    host = document.createElement('div');
    document.body.appendChild(host);
    root = createRoot(host);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await act(async () => {
      root!.render(
        createElement(
          QueryClientProvider,
          { client },
          createElement(
            MantineProvider,
            { theme },
            createElement(MuscleGroupProgressionPanel, { window: { window: '30d' } as never }),
          ),
        ),
      );
    });
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });
    const hrefs = (nome: string) =>
      Array.from(host.querySelectorAll('a'))
        .filter((a) => a.textContent === nome)
        .map((a) => a.getAttribute('href'));
    expect(hrefs('Supino')).toEqual(['/dashboard/exercicios/11']);
    expect(hrefs('Remada')).toEqual(['/dashboard/exercicios/12']);
    expect(hrefs('Agachamento')).toEqual(['/dashboard/exercicios/13']);
    expect(hrefs('Rosca')).toEqual(['/dashboard/exercicios/14']);
    expect(host.textContent).toContain('Sem id');
    expect(hrefs('Sem id')).toEqual([]);
  });
});
