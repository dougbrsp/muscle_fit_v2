import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';

// M56: só 401 desloga. 503 e erro de rede tentam de novo (GET/HEAD) e, se
// persistirem, viram erro para a tela — nunca `clearSession` + redirect.

vi.mock('./auth', () => ({ clearSession: vi.fn() }));

type ApiModule = typeof import('./api');

let api: ApiModule;
let clearSession: Mock<() => void>;
let fetchMock: Mock<typeof fetch>;
let location: { pathname: string; search: string; href: string };

function json(status: number, body: unknown = {}): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

/** Roda a chamada até o fim, avançando os timers do retry. */
async function settle<T>(promise: Promise<T>): Promise<PromiseSettledResult<T>> {
  const result = Promise.allSettled([promise]);
  await vi.runAllTimersAsync();
  return (await result)[0];
}

function redirectedToLogin(): boolean {
  return location.href !== '';
}

beforeEach(async () => {
  // Módulo novo por teste: o flag `redirectingToLogin` é estado de módulo.
  vi.resetModules();
  vi.useFakeTimers();
  location = { pathname: '/dashboard/', search: '', href: '' };
  vi.stubGlobal('window', { location });
  fetchMock = vi.fn<typeof fetch>();
  vi.stubGlobal('fetch', fetchMock);
  api = await import('./api');
  clearSession = vi.mocked((await import('./auth')).clearSession);
  clearSession.mockClear();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('apiFetch — 401 é o único que desloga', () => {
  it('401 limpa a sessão e redireciona para o login com next', async () => {
    fetchMock.mockResolvedValue(json(401, { detail: 'Token inválido.' }));

    const result = await settle(api.apiFetch('/auth/me'));

    expect(result.status).toBe('rejected');
    expect((result as PromiseRejectedResult).reason).toMatchObject({ status: 401 });
    expect(clearSession).toHaveBeenCalledTimes(1);
    expect(location.href).toBe('/?next=%2Fdashboard%2F');
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('503 persistente num GET: tenta de novo, falha com 503 e NÃO desloga', async () => {
    fetchMock.mockImplementation(async () => json(503, { detail: 'Não foi possível validar sua sessão agora.' }));

    const result = await settle(api.apiFetch('/auth/me'));

    expect(fetchMock).toHaveBeenCalledTimes(api.TRANSIENT_RETRY_DELAYS_MS.length + 1);
    expect(result.status).toBe('rejected');
    const error = (result as PromiseRejectedResult).reason;
    expect(error).toBeInstanceOf(api.ApiError);
    expect(error.status).toBe(503);
    expect(api.isTransientApiError(error)).toBe(true);
    expect(clearSession).not.toHaveBeenCalled();
    expect(redirectedToLogin()).toBe(false);
  });

  it('503 seguido de sucesso: o GET se recupera sozinho', async () => {
    fetchMock.mockResolvedValueOnce(json(503)).mockResolvedValueOnce(json(200, { id: 1 }));

    const result = await settle(api.apiFetch<{ id: number }>('/auth/me'));

    expect(result).toEqual({ status: 'fulfilled', value: { id: 1 } });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(clearSession).not.toHaveBeenCalled();
  });

  it('erro de rede num GET: tenta de novo, falha com status 0 e NÃO desloga', async () => {
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'));

    const result = await settle(api.apiFetch('/auth/me'));

    expect(fetchMock).toHaveBeenCalledTimes(api.TRANSIENT_RETRY_DELAYS_MS.length + 1);
    expect((result as PromiseRejectedResult).reason).toMatchObject({ status: 0 });
    expect(clearSession).not.toHaveBeenCalled();
    expect(redirectedToLogin()).toBe(false);
  });

  it('503 num POST: NÃO repete a escrita e NÃO desloga', async () => {
    fetchMock.mockResolvedValue(json(503));

    const result = await settle(api.apiFetch('/workout-sessions/1/finish', { method: 'POST', body: '{}' }));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect((result as PromiseRejectedResult).reason).toMatchObject({ status: 503 });
    expect(clearSession).not.toHaveBeenCalled();
    expect(redirectedToLogin()).toBe(false);
  });

  it('500 não é transitório: sem retry, sem deslogar', async () => {
    fetchMock.mockResolvedValue(json(500));

    const result = await settle(api.apiFetch('/analytics/x'));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect((result as PromiseRejectedResult).reason).toMatchObject({ status: 500 });
    expect(clearSession).not.toHaveBeenCalled();
    expect(redirectedToLogin()).toBe(false);
  });

  it('apiFetchBlob: 503 não desloga, 401 desloga', async () => {
    fetchMock.mockResolvedValue(json(503));
    const indisponivel = await settle(api.apiFetchBlob('/users/1/photo'));
    expect((indisponivel as PromiseRejectedResult).reason).toMatchObject({ status: 503 });
    expect(clearSession).not.toHaveBeenCalled();

    fetchMock.mockResolvedValue(json(401));
    const expirada = await settle(api.apiFetchBlob('/users/1/photo'));
    expect((expirada as PromiseRejectedResult).reason).toMatchObject({ status: 401 });
    expect(clearSession).toHaveBeenCalledTimes(1);
  });
});

describe('apiFetch — detail estruturado', () => {
  it('detail string continua sendo a mensagem; sem detail estruturado', async () => {
    fetchMock.mockResolvedValue(json(409, { detail: 'Conflito.' }));
    const result = await settle(api.apiFetch('/x', { method: 'POST', body: '{}' }));
    const error = (result as PromiseRejectedResult).reason;
    expect(error.message).toBe('Conflito.');
    expect(error.detail).toBeUndefined();
  });

  it('detail objeto ({error, message}) fica em `detail`; a mensagem genérica não muda', async () => {
    const detail = { error: 'exercise_card_generation_in_progress', message: 'Já existe.', generation_id: 11 };
    fetchMock.mockResolvedValue(json(409, { detail }));
    const result = await settle(api.apiFetch('/exercises/1/card-generations', { method: 'POST', body: '{}' }));
    const error = (result as PromiseRejectedResult).reason;
    expect(error).toBeInstanceOf(api.ApiError);
    expect(error.status).toBe(409);
    expect(error.message).toBe('Erro ao consultar /exercises/1/card-generations (HTTP 409).');
    expect(error.detail).toEqual(detail);
  });
});

describe('apiFetch — erros da troca de senha não deslogam (bug de 2026-10-10)', () => {
  const body = JSON.stringify({ current_password: 'temporaria', new_password: 'NovaSenha123' });

  it.each([
    [400, { detail: 'Senha atual incorreta.', code: 'wrong_current_password' }],
    [429, { detail: 'Muitas tentativas com senha incorreta.', code: 'account_locked' }],
  ])('HTTP %i com code vira erro para a tela, sem redirect', async (status, payload) => {
    location.pathname = '/perfil';
    location.search = '?troque-senha=1';
    fetchMock.mockResolvedValue(json(status, payload));

    const result = await settle(api.apiFetch('/auth/change-password', { method: 'POST', body }));

    expect(result.status).toBe('rejected');
    const error = (result as PromiseRejectedResult).reason;
    expect(error).toBeInstanceOf(api.ApiError);
    expect(error.status).toBe(status);
    expect(error.message).toBe(payload.detail);
    expect(clearSession).not.toHaveBeenCalled();
    expect(redirectedToLogin()).toBe(false);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe('classifySessionCheckError (guarda de /auth/me nos layouts)', () => {
  it('só 401 é "deslogado"; 503, rede e o resto são "indisponível"', async () => {
    const { classifySessionCheckError } = await import('./session-guard');
    expect(classifySessionCheckError(new api.ApiError('x', 401))).toBe('unauthenticated');
    expect(classifySessionCheckError(new api.ApiError('x', 503))).toBe('unavailable');
    expect(classifySessionCheckError(new api.ApiError('x', 0))).toBe('unavailable');
    expect(classifySessionCheckError(new api.ApiError('x', 500))).toBe('unavailable');
    expect(classifySessionCheckError(new Error('render'))).toBe('unavailable');
  });
});
