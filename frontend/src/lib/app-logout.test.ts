import { QueryClient, QueryObserver } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const calls: string[] = [];

let loggingOut = false;
vi.mock('@/lib/api', () => ({
  beginLogout: vi.fn(() => {
    loggingOut = true;
  }),
  endLogout: vi.fn(() => {
    loggingOut = false;
  }),
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
import { clearSession, confirmLogoutWithUnsyncedOps } from '@/lib/auth';
import { logoutAndRedirect, resetSessionCache } from './app-logout';

const PERMS = { id: 1, permissions: { admin: { ver: 'todos' } } };

describe('logoutAndRedirect', () => {
  beforeEach(() => {
    calls.length = 0;
    loggingOut = false;
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

describe('flag de saída em andamento', () => {
  beforeEach(() => {
    calls.length = 0;
    loggingOut = false;
    vi.mocked(confirmLogoutWithUnsyncedOps).mockResolvedValue(true);
  });

  it('liga depois da confirmação, antes de clearSession, e segue ligada após a navegação', async () => {
    const qc = new QueryClient();
    let atClear = false;
    vi.mocked(clearSession).mockImplementationOnce(async () => {
      atClear = loggingOut;
    });
    await logoutAndRedirect(vi.fn(), qc);
    expect(atClear).toBe(true);
    expect(loggingOut).toBe(true);
  });

  it('cancelar a confirmação não liga a flag', async () => {
    vi.mocked(confirmLogoutWithUnsyncedOps).mockResolvedValue(false);
    await logoutAndRedirect(vi.fn(), new QueryClient());
    expect(loggingOut).toBe(false);
  });

  it('replace que lança desliga a flag', async () => {
    const replace = vi.fn(() => {
      throw new Error('nav');
    });
    await expect(logoutAndRedirect(replace, new QueryClient())).rejects.toThrow('nav');
    expect(loggingOut).toBe(false);
  });

  it('resetSessionCache (fronteira do login) desliga a flag', () => {
    loggingOut = true;
    resetSessionCache(new QueryClient());
    expect(loggingOut).toBe(false);
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
