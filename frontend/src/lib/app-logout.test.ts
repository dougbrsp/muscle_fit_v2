import { QueryClient, QueryObserver } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const calls: string[] = [];

vi.mock('@/lib/api', () => ({
  apiFetch: vi.fn(async () => {
    calls.push('apiFetch');
    return {};
  }),
}));
vi.mock('@/lib/auth', () => ({
  clearSession: vi.fn(() => {
    calls.push('clearSession');
  }),
  confirmLogoutWithUnsyncedOps: vi.fn(async () => true),
}));

import { apiFetch } from '@/lib/api';
import { confirmLogoutWithUnsyncedOps } from '@/lib/auth';
import { logoutAndRedirect, resetSessionCache } from './app-logout';

const PERMS = { id: 1, permissions: { admin: { ver: 'todos' } } };

describe('logoutAndRedirect', () => {
  beforeEach(() => {
    calls.length = 0;
    vi.mocked(confirmLogoutWithUnsyncedOps).mockResolvedValue(true);
    vi.mocked(apiFetch).mockImplementation((async () => {
      calls.push('apiFetch');
      return {};
    }) as never);
  });

  it('cancela as queries e navega sem limpar o cache (clearSession -> cancel -> replace)', async () => {
    const qc = new QueryClient();
    qc.setQueryData(['current-user'], PERMS);
    const cancel = vi.spyOn(qc, 'cancelQueries').mockImplementation(async () => {
      calls.push('cancelQueries');
    });
    const replace = vi.fn((path: string) => {
      calls.push(`replace:${path}:cache=${qc.getQueryCache().getAll().length}`);
    });

    await logoutAndRedirect(replace, qc);

    expect(cancel).toHaveBeenCalledTimes(1);
    expect(calls).toEqual(['apiFetch', 'clearSession', 'cancelQueries', 'replace:/:cache=1']);
    expect(qc.getQueryData(['current-user'])).toEqual(PERMS);
  });

  it('observer de current-user nao refaz /auth/me antes da navegacao', async () => {
    const qc = new QueryClient();
    qc.setQueryData(['current-user'], PERMS);
    const queryFn = vi.fn(async () => {
      calls.push('GET /auth/me');
      return PERMS;
    });
    const observer = new QueryObserver(qc, {
      queryKey: ['current-user'],
      queryFn,
      staleTime: Infinity,
    });
    const unsubscribe = observer.subscribe(() => {});
    const replace = vi.fn(() => {
      calls.push('replace');
    });

    await logoutAndRedirect(replace, qc);
    await Promise.resolve();

    expect(queryFn).not.toHaveBeenCalled();
    expect(calls).toEqual(['apiFetch', 'clearSession', 'replace']);
    expect(qc.getQueryCache().find({ queryKey: ['current-user'] })).toBeDefined();
    unsubscribe();
  });

  it('POST /auth/logout falha: ainda faz clearSession, cancelamento e replace', async () => {
    vi.mocked(apiFetch).mockRejectedValueOnce(new Error('rede'));
    const qc = new QueryClient();
    vi.spyOn(qc, 'cancelQueries').mockImplementation(async () => {
      calls.push('cancelQueries');
    });
    const replace = vi.fn(() => {
      calls.push('replace');
    });

    await logoutAndRedirect(replace, qc);

    expect(calls).toEqual(['clearSession', 'cancelQueries', 'replace']);
  });

  it('cancelQueries que rejeita ainda navega', async () => {
    const qc = new QueryClient();
    vi.spyOn(qc, 'cancelQueries').mockRejectedValue(new Error('x'));
    const replace = vi.fn();

    await expect(logoutAndRedirect(replace, qc)).resolves.toBeUndefined();
    expect(replace).toHaveBeenCalledWith('/');
  });

  it('cancelar a confirmacao mantem sessao e cache', async () => {
    vi.mocked(confirmLogoutWithUnsyncedOps).mockResolvedValue(false);
    const qc = new QueryClient();
    qc.setQueryData(['current-user'], { id: 1 });
    const replace = vi.fn();

    await logoutAndRedirect(replace, qc);

    expect(replace).not.toHaveBeenCalled();
    expect(calls).toEqual([]);
    expect(qc.getQueryData(['current-user'])).toEqual({ id: 1 });
  });
});

describe('resetSessionCache (fronteira do login)', () => {
  it('descarta todo o cache da conta anterior', () => {
    const qc = new QueryClient();
    qc.setQueryData(['current-user'], PERMS);
    qc.setQueryData(['users-admin'], [{ id: 2 }]);

    resetSessionCache(qc);

    expect(qc.getQueryCache().getAll()).toHaveLength(0);
    expect(qc.getQueryData(['current-user'])).toBeUndefined();
  });
});
