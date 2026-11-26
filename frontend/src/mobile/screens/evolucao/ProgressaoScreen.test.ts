// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { EMPTY_DASHBOARD_FILTERS, type DashboardFilters } from '@/lib/dashboard-filters';
import { exerciseProgressHref } from '@/lib/exercise-progress';
import { ProgressaoScreen } from './ProgressaoScreen';

// Links de progresso por exercício (grupo, força e detalhe): o href vem de exerciseProgressHref e preserva os filtros.

const FILTROS: DashboardFilters = { ...EMPTY_DASHBOARD_FILTERS, window: '90d', exerciseMappingId: 5, exerciseName: 'Supino reto' };

vi.mock('@/lib/dashboard-api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/dashboard-api')>()),
  fetchProgressionByMuscleGroup: async () => ({
    degraded: false,
    min_sessions_per_window: 2,
    items: [
      {
        muscle_group_id: 1,
        muscle_group_name: 'Peito',
        performance_exercises_excluded: 0,
        performance_exercises: [{ exercise_id: 7, exercise_name: 'Supino inclinado', status: 'PROGRESSING' }],
      },
    ],
  }),
  fetchStrengthPanel: async () => ({ lifts: [] }),
  fetchExerciseOptions: async () => ({ items: [] }),
  fetchProgression: async () => ({ items: [] }),
  fetchProgressionTrend: async () => null,
  fetchProgressionSuggestion: async () => null,
}));
vi.mock('@/lib/strength-panel', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/strength-panel')>()),
  strengthPanelRows: () => [
    {
      exerciseId: 9,
      name: 'Agachamento',
      strengthSessions: 3,
      latestE1rm: 100,
      latestE1rmDate: null,
      bestE1rm: 110,
      bestE1rmReliable: true,
      lastTopSet: null,
      chart: { hasSeries: false },
    },
  ],
}));
vi.mock('@/lib/dashboard-filters', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/dashboard-filters')>()),
  useDashboardFilters: () => ({ filters: FILTROS, setFilters: () => {}, dayRange: 90, windowQuery: {}, rangeError: null }),
}));
vi.mock('@/lib/training-goals', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/training-goals')>()),
  useTrainingGoals: () => ({ data: null }),
}));
vi.mock('next/navigation', () => ({ useRouter: () => ({}), usePathname: () => '/', useSearchParams: () => new URLSearchParams() }));

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
  Element.prototype.scrollIntoView ||= () => {};
});

beforeEach(async () => {
  host = document.createElement('div');
  document.body.appendChild(host);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  root = createRoot(host);
  await act(async () => {
    root!.render(createElement(QueryClientProvider, { client }, createElement(MantineProvider, { env: 'test' }, createElement(ProgressaoScreen))));
  });
  await act(async () => {
    await new Promise((r) => setTimeout(r, 50));
  });
});

afterEach(async () => {
  await act(async () => root?.unmount());
  root = null;
  host.remove();
  document.body.innerHTML = '';
});

const hrefDe = (texto: string) =>
  Array.from(document.querySelectorAll('a')).find((a) => a.textContent?.includes(texto))?.getAttribute('href');

describe('ProgressaoScreen mobile: links de progresso do exercício', () => {
  it('Força e detalhe usam exerciseProgressHref com os filtros na URL', () => {
    expect(hrefDe('Agachamento')).toBe(exerciseProgressHref(9, FILTROS));
    expect(hrefDe('Ver progresso com comparabilidade')).toBe(exerciseProgressHref(5, FILTROS));
    expect(hrefDe('Agachamento')).toContain('window=90d');
    expect(hrefDe('Ver progresso com comparabilidade')).toContain('exerciseName=');
  });

  it('o link do exercício no detalhe do grupo usa exerciseProgressHref com os filtros', async () => {
    const card = Array.from(document.querySelectorAll<HTMLElement>('button, [role="button"]')).find((b) => b.textContent?.includes('Peito'))!;
    await act(async () => card.click());
    await act(async () => {
      await new Promise((r) => setTimeout(r, 50));
    });
    expect(hrefDe('Supino inclinado')).toBe(exerciseProgressHref(7, FILTROS));
    expect(hrefDe('Supino inclinado')).toContain('window=90d');
  });
});
