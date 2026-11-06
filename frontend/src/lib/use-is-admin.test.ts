import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement, type ReactElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { PartialState } from '@/components/dashboard/states';
import { MetricValue } from '@/components/dashboard/MetricValue';
import type { Metric } from '@/lib/dashboard-v3-types';
import { theme } from '@/theme';
import { useIsAdmin } from './use-is-admin';

/** Render SSR com o cache de ['current-user'] pré-carregado (sem jsdom). */
function render(element: ReactElement, role: string | null): string {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  if (role) client.setQueryData(['current-user'], { id: 1, username: 'u', role });
  return renderToStaticMarkup(
    createElement(
      QueryClientProvider,
      { client },
      createElement(MantineProvider, { theme }, element),
    ),
  );
}

function Probe() {
  return createElement('span', null, useIsAdmin() ? 'admin' : 'nao-admin');
}

const metric = {
  value: 1,
  unit: 'kg',
  nature: 'OBSERVED',
  data_quality: 'MEDIUM',
  limitations: [],
} as unknown as Metric;

describe('useIsAdmin', () => {
  it('true só para role admin', () => {
    expect(render(createElement(Probe), 'admin')).toContain('>admin<');
    expect(render(createElement(Probe), 'user')).toContain('nao-admin');
  });
  it('false enquanto carrega (sem dado no cache)', () => {
    expect(render(createElement(Probe), null)).toContain('nao-admin');
  });
});

describe('PartialState (confiança só para admin)', () => {
  const el = () => createElement(PartialState, { quality: 'MEDIUM', limitations: ['Poucos dados'] });
  it('admin vê o alerta', () => {
    const html = render(el(), 'admin');
    expect(html).toContain('Leitura parcial');
    expect(html).toContain('Poucos dados');
  });
  it('não-admin não vê', () => {
    const html = render(el(), 'user');
    expect(html).not.toContain('Leitura parcial');
    expect(html).not.toContain('Poucos dados');
  });
  it('loading não vê', () => {
    const html = render(el(), null);
    expect(html).not.toContain('Leitura parcial');
    expect(html).not.toContain('Poucos dados');
  });
});

describe('MetricValue (selo de confiança só para admin)', () => {
  const el = () => createElement(MetricValue, { metric });
  it('admin vê o selo; não-admin e loading não', () => {
    const admin = render(el(), 'admin');
    const user = render(el(), 'user');
    expect(admin.length).toBeGreaterThan(user.length);
    expect(render(el(), null)).toBe(user);
  });
});
