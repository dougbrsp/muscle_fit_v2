// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { theme } from '@/theme';
import { DashboardProgressaoClient } from './DashboardProgressaoClient';

const progression = vi.fn();

vi.mock('@/lib/dashboard-api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/dashboard-api')>()),
  fetchExerciseOptions: async () => ({ items: [] }),
  fetchProgression: (...args: unknown[]) => progression(...args),
  fetchProgressionTrend: async () => null,
  fetchProgressionSuggestion: async () => null,
}));
vi.mock('@/lib/dashboard-filters', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/dashboard-filters')>()),
  useDashboardFilters: () => ({
    filters: { exerciseMappingId: 5, exerciseName: 'Supino reto' },
    setFilters: () => {},
    dayRange: 30,
    windowQuery: {},
    rangeError: null,
  }),
}));
vi.mock('@/lib/training-goals', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/training-goals')>()),
  useTrainingGoals: () => ({ data: null }),
}));
vi.mock('next/navigation', () => ({ useRouter: () => ({}), usePathname: () => '/', useSearchParams: () => new URLSearchParams() }));
vi.mock('./MuscleGroupProgressionPanel', () => ({ MuscleGroupProgressionPanel: () => null }));
vi.mock('./StrengthPanel', () => ({ StrengthPanel: () => null }));
vi.mock('./ProgressaoChart', () => ({ ProgressaoChart: () => null }));

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

beforeEach(() => progression.mockReset());

afterEach(async () => {
  await act(async () => {
    root?.unmount();
  });
  root = null;
  host?.remove();
});

// Dado de exemplo de resposta da API (a tela só exibe).
const session = (over: Record<string, unknown> = {}) => ({
  activity_id: 1,
  local_date: '2026-11-10',
  sets_count: 3,
  avg_rpe: 8,
  e1rm: null,
  ...over,
});

async function render(items: unknown[]) {
  progression.mockResolvedValue({ items });
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root!.render(
      createElement(
        QueryClientProvider,
        { client: new QueryClient({ defaultOptions: { queries: { retry: false } } }) },
        createElement(MantineProvider, { theme }, createElement(DashboardProgressaoClient)),
      ),
    );
  });
  // Deixa as consultas simuladas resolverem.
  await act(async () => {
    await new Promise((r) => setTimeout(r, 50));
  });
}

describe('DashboardProgressaoClient: RPE vs esperado', () => {
  it('mostra a contagem com os rótulos vindos da API', async () => {
    await render([
      session({
        rpe_vs_expected_counts: { below: 1, on_target: 2, above: 0, compared: 3 },
        rpe_vs_expected_labels: { below: 'Abaixo do esperado', on_target: 'No esperado', above: 'Acima do esperado' },
      }),
    ]);
    expect(host.textContent).toContain('RPE vs esperado: 3 séries comparadas: 1 abaixo do esperado, 2 no esperado');
  });

  it('sem os campos, não mostra a linha', async () => {
    await render([session()]);
    expect(host.textContent).toContain('Detalhe por exercício');
    expect(host.textContent).not.toContain('RPE vs esperado');
  });

  it('sem série comparada, não mostra a linha', async () => {
    await render([session({ rpe_vs_expected_counts: { below: 0, on_target: 0, above: 0, compared: 0 } })]);
    expect(host.textContent).not.toContain('RPE vs esperado');
  });
});
