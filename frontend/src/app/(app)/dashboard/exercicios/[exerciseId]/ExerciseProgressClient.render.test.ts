// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { theme } from '@/theme';
import { ExerciseProgressContent } from './ExerciseProgressClient';

vi.mock('next/navigation', () => ({ useParams: () => ({}), useRouter: () => ({}), usePathname: () => '/' }));

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

// Dado de exemplo de resposta da API (a tela só exibe).
const SCOPE = {
  code: 'exercise_window_e1rm_trend',
  entity: 'exercise',
  metric: 'e1rm_mean',
  comparator: 'first_half_vs_second_half_of_window',
  label: 'Primeira metade vs. segunda da janela',
  description: 'Compara a média de e1RM da primeira metade com a da segunda.',
};

const trend = (over: Record<string, unknown> = {}) => ({
  status: 'PROGRESSING',
  label: null,
  reasons: [],
  change_pct: 0.1,
  initial_mean: 50,
  final_mean: 55,
  exposures_used: 4,
  rule_version: 'v1',
  scope: SCOPE,
  ...over,
});

const data = (over: Record<string, unknown> = {}) => ({
  window: { date_from: '2026-10-01', date_to: '2026-11-01' },
  exercise: { exercise_id: 7, name: 'Supino reto', amazfit_name: null },
  comparability_key: null,
  status: 'PROGRESSING',
  status_scope: null,
  status_reasons: [],
  exposures: [],
  records: {},
  e1rm_series: [],
  deload_sessions: [],
  progression_decisions: [],
  metric_versions: {},
  data_quality: 'ok',
  limitations: [],
  ...over,
});

async function render(body: unknown) {
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root!.render(
      createElement(
        QueryClientProvider,
        { client: new QueryClient({ defaultOptions: { queries: { retry: false, enabled: false } } }) },
        createElement(
          MantineProvider,
          { theme },
          createElement(ExerciseProgressContent, { data: body as never, defaultGoal: null }),
        ),
      ),
    );
  });
}

describe('ExerciseProgressContent: tendência da janela', () => {
  it('mostra a Leitura atual e o bloco com título do escopo e descrição acessível', async () => {
    await render(data({ window_trend: trend() }));
    expect(host.textContent).toContain('Leitura atual');
    const block = host.querySelector('[data-testid="window-trend"]');
    expect(block).not.toBeNull();
    const title = host.querySelector('[data-testid="window-trend-title"]') as HTMLElement;
    expect(title.textContent).toBe(SCOPE.label);
    expect(title.getAttribute('tabindex')).toBe('0');
    expect(title.getAttribute('aria-label')).toBe(`${SCOPE.label}. ${SCOPE.description}`);
    expect(title.hasAttribute('title')).toBe(false);
  });

  it('sem descrição, o título não vira foco nem nota', async () => {
    await render(data({ window_trend: trend({ scope: { ...SCOPE, description: '' } }) }));
    const title = host.querySelector('[data-testid="window-trend-title"]') as HTMLElement;
    expect(title.hasAttribute('tabindex')).toBe(false);
    expect(title.hasAttribute('aria-label')).toBe(false);
  });

  it('resposta sem o campo não mostra o bloco', async () => {
    await render(data());
    expect(host.textContent).toContain('Leitura atual');
    expect(host.querySelector('[data-testid="window-trend"]')).toBeNull();
  });
});
