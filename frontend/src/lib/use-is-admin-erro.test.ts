import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { useIsAdmin } from './use-is-admin';

function Probe() {
  return createElement('span', null, useIsAdmin() ? 'admin' : 'nao-admin');
}

describe('useIsAdmin em erro de /auth/me', () => {
  it('consulta falha: fica oculto (false), mesmo com role admin velho nos dados', () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    client
      .getQueryCache()
      .build(client, { queryKey: ['current-user'] })
      .setState({ status: 'error', error: new Error('401'), data: undefined });
    const html = renderToStaticMarkup(
      createElement(QueryClientProvider, { client }, createElement(Probe)),
    );
    expect(html).toContain('nao-admin');
  });
});
