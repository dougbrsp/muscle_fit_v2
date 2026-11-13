import { beforeEach, describe, expect, it, vi } from 'vitest';

const apiFetch = vi.fn();
vi.mock('./api', async (importOriginal) => ({ ...(await importOriginal<typeof import('./api')>()), apiFetch }));

const api = await import('./access-admin-api');
const { ApiError } = await import('./api');

describe('access-admin-api: transporte para /access', () => {
  beforeEach(() => {
    apiFetch.mockReset();
    apiFetch.mockResolvedValue({});
  });

  it('usa caminhos e métodos do contrato', async () => {
    await api.fetchAccessCatalog();
    await api.fetchAccessGroups();
    await api.createAccessGroup({ group_key: 'abc', name: 'A' });
    await api.updateAccessGroup(3, { name: 'B', description: null });
    await api.replaceAccessGrants(3, [{ resource: 'r', action: 'ver', scope: 'todos' }]);
    await api.deleteAccessGroup(3);
    await api.fetchGroupMembers(3);
    await api.addGroupMember(3, 9);
    await api.removeGroupMember(3, 9);
    await api.fetchGroupAudit(3);
    expect(apiFetch.mock.calls).toEqual([
      ['/access/catalog'],
      ['/access/groups'],
      ['/access/groups', { method: 'POST', body: JSON.stringify({ group_key: 'abc', name: 'A' }) }],
      ['/access/groups/3', { method: 'PUT', body: JSON.stringify({ name: 'B', description: null }) }],
      [
        '/access/groups/3/grants',
        { method: 'PUT', body: JSON.stringify({ grants: [{ resource: 'r', action: 'ver', scope: 'todos' }] }) },
      ],
      ['/access/groups/3', { method: 'DELETE' }],
      ['/access/groups/3/members'],
      ['/access/groups/3/members', { method: 'POST', body: JSON.stringify({ user_id: 9 }) }],
      ['/access/groups/3/members/9', { method: 'DELETE' }],
      ['/access/audit?group_id=3&limit=50&offset=0'],
    ]);
  });

  it('query keys são estáveis por grupo', () => {
    expect(api.accessKeys.members(2)).toEqual(['access-admin', 'members', 2]);
    expect(api.accessKeys.audit(2)).toEqual(['access-admin', 'audit', 2]);
  });

  it('corpo real do backend ({error, message} de topo) passa por apiFetch até accessErrorMessage', async () => {
    const real = await vi.importActual<typeof import('./api')>('./api');
    vi.stubGlobal('window', { location: { pathname: '/', search: '', href: '' } });
    const fetchMock = vi.fn(
      async () =>
        new Response(JSON.stringify({ error: 'group_in_use', message: 'Grupo em uso.' }), {
          status: 409,
          headers: { 'Content-Type': 'application/json' },
        }),
    );
    vi.stubGlobal('fetch', fetchMock);
    try {
      const error = await real.apiFetch('/access/groups/3', { method: 'DELETE' }).catch((e: unknown) => e);
      expect(error).toBeInstanceOf(real.ApiError);
      expect(api.accessErrorMessage(error, 'f')).toBe('Grupo em uso.');
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('accessErrorMessage prefere detail.message e cai no fallback', () => {
    expect(api.accessErrorMessage(new ApiError('genérica', 409, { error: 'x', message: 'Texto do backend' }), 'f')).toBe(
      'Texto do backend',
    );
    expect(api.accessErrorMessage(new ApiError('texto simples', 400), 'f')).toBe('texto simples');
    expect(api.accessErrorMessage(new Error('x'), 'fallback')).toBe('fallback');
  });

  it('accessErrorMessage usa o fallback em PT-BR para detalhe estruturado sem message (422)', () => {
    const lista = [{ loc: ['body', 'name'], msg: 'field required', type: 'missing' }];
    expect(api.accessErrorMessage(new ApiError('Erro ao consultar /access/groups', 422, lista), 'Falha em português')).toBe(
      'Falha em português',
    );
    expect(api.accessErrorMessage(new ApiError('Erro', 422, { error: 'x' }), 'Falha em português')).toBe('Falha em português');
  });
});
