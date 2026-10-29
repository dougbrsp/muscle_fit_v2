import { afterEach, describe, expect, it, vi } from 'vitest';

import { DEFAULT_API_BASE_URL, getApiBaseUrl, resolveApiBaseUrl } from './config';

// Marcador `{hostname}` em NEXT_PUBLIC_API_BASE_URL (DEV desktop + celular via
// Tailscale). Sem marcador — e em produção, onde a variável não é definida —
// o valor tem de ser idêntico ao de antes.

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

function stubWindowHostname(hostname: string): void {
  vi.stubGlobal('window', { location: { hostname } });
}

describe('resolveApiBaseUrl', () => {
  it('sem marcador devolve o valor como está, ignorando o host da página', () => {
    expect(resolveApiBaseUrl('http://localhost:8000', 'devbox.tailnet-exemplo.ts.net')).toBe('http://localhost:8000');
  });

  it('sem variável cai no default de produção', () => {
    expect(resolveApiBaseUrl(undefined, 'musclefit.com.br')).toBe('https://api.musclefit.com.br');
    expect(resolveApiBaseUrl(undefined, undefined)).toBe(DEFAULT_API_BASE_URL);
  });

  it('remove barras finais', () => {
    expect(resolveApiBaseUrl('https://api.exemplo.com//', undefined)).toBe('https://api.exemplo.com');
    expect(resolveApiBaseUrl('http://{hostname}:8000/', 'localhost')).toBe('http://localhost:8000');
  });

  it('com marcador troca pelo hostname e, sem hostname, por localhost', () => {
    expect(resolveApiBaseUrl('http://{hostname}:8000', 'devbox.tailnet-exemplo.ts.net')).toBe(
      'http://devbox.tailnet-exemplo.ts.net:8000',
    );
    expect(resolveApiBaseUrl('http://{hostname}:8000', undefined)).toBe('http://localhost:8000');
    expect(resolveApiBaseUrl('http://{hostname}:8000', '')).toBe('http://localhost:8000');
  });
});

describe('getApiBaseUrl', () => {
  it('produção: variável ausente dá a URL fixa mesmo com window', () => {
    vi.stubEnv('NEXT_PUBLIC_API_BASE_URL', undefined);
    stubWindowHostname('musclefit.com.br');
    expect(getApiBaseUrl()).toBe('https://api.musclefit.com.br');
  });

  it('sem marcador: valor idêntico ao configurado', () => {
    vi.stubEnv('NEXT_PUBLIC_API_BASE_URL', 'http://localhost:8000/');
    stubWindowHostname('devbox.tailnet-exemplo.ts.net');
    expect(getApiBaseUrl()).toBe('http://localhost:8000');
  });

  it('com marcador no browser: usa window.location.hostname a cada chamada', () => {
    vi.stubEnv('NEXT_PUBLIC_API_BASE_URL', 'http://{hostname}:8000');
    stubWindowHostname('localhost');
    expect(getApiBaseUrl()).toBe('http://localhost:8000');
    stubWindowHostname('devbox.tailnet-exemplo.ts.net');
    expect(getApiBaseUrl()).toBe('http://devbox.tailnet-exemplo.ts.net:8000');
  });

  it('com marcador sem window (SSR/export): localhost', () => {
    vi.stubEnv('NEXT_PUBLIC_API_BASE_URL', 'http://{hostname}:8000');
    expect(typeof window).toBe('undefined');
    expect(getApiBaseUrl()).toBe('http://localhost:8000');
  });
});
