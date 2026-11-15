import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { permissionsApi, usePermissions } from './use-permissions';

function render(user: unknown | null, failRefetch = false): string {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  if (user) client.setQueryData(['current-user'], user);
  if (user && failRefetch) {
    // Estado do TanStack após refetch falho: status 'error' com `data` mantido.
    client.getQueryCache().find({ queryKey: ['current-user'] })!.setState({
      status: 'error',
      error: new Error('503'),
      fetchStatus: 'idle',
    });
  }
  function Probe() {
    const p = usePermissions();
    return createElement('span', null, `${p.ready}|${p.can('dashboards')}|${p.can('dashboards', 'ver', 'todos')}`);
  }
  return renderToStaticMarkup(createElement(QueryClientProvider, { client }, createElement(Probe)));
}

describe('permissionsApi', () => {
  const user = { permissions: { dashboards: { ver: 'proprios' } } };
  it('com sucesso e permissões, aplica can', () => {
    const api = permissionsApi(user);
    expect(api.ready).toBe(true);
    expect(api.can('dashboards')).toBe(true);
    expect(api.can('dashboards', 'ver', 'todos')).toBe(false);
    expect(api.can('admin.grupos')).toBe(false);
  });
  it('sem dados (carregando ou erro sem cache): tudo false', () => {
    const api = permissionsApi(undefined);
    expect(api.ready).toBe(false);
    expect(api.can('dashboards')).toBe(false);
  });
  it('permissions null ou ausente: tudo false', () => {
    expect(permissionsApi({ permissions: null }).can('dashboards')).toBe(false);
    expect(permissionsApi({}).can('dashboards')).toBe(false);
  });
});

describe('usePermissions', () => {
  it('lê o cache de [current-user]', () => {
    expect(render({ id: 1, role: 'user', permissions: { dashboards: { ver: 'proprios' } } })).toContain(
      'true|true|false',
    );
  });
  it('sem dado no cache (carregando): false', () => {
    expect(render(null)).toContain('false|false|false');
  });
  it('refetch falho com dados em cache (isError=true): mantém permissões', () => {
    const user = { id: 1, role: 'user', permissions: { dashboards: { ver: 'proprios' } } };
    expect(render(user, true)).toContain('true|true|false');
  });
  it('permissions null: false', () => {
    expect(render({ id: 1, role: 'admin', permissions: null })).toContain('true|false|false');
  });
});
