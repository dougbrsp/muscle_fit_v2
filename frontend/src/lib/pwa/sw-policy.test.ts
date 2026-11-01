import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

import { shouldRegisterServiceWorker, shouldUnregisterServiceWorker } from './sw-register';
import { tryApplyUpdate } from './update-gate';
import { shouldOfferUpdate } from './update-policy';

type Input = {
  method: string;
  url: string;
  origin: string;
  mode?: string;
  destination?: string;
  credentials?: string;
  hasAuthorization?: boolean;
  hasRange?: boolean;
};
type Policy = {
  decideStrategy: (i: Input) => string;
  STRATEGY: { BYPASS: string; STATIC: string; NAVIGATION: string; IMAGE: string };
  isCacheable: (r: unknown) => boolean;
  extractStaticUrls: (html: string) => string[];
};

// Carrega o public/sw.js REAL (o mesmo arquivo publicado) num escopo falso.
function loadPolicy(): Policy {
  const source = readFileSync(fileURLToPath(new URL('../../../public/sw.js', import.meta.url)), 'utf8');
  const fakeSelf: Record<string, unknown> = { addEventListener: () => undefined, location: { origin: 'https://app.musclefit.com.br' } };
  new Function('self', source)(fakeSelf);
  return fakeSelf.__musclefitSwPolicy as Policy;
}

const ORIGIN = 'https://app.musclefit.com.br';
const { decideStrategy, STRATEGY, isCacheable, extractStaticUrls } = loadPolicy();
const decide = (over: Partial<Input> & { path?: string; url?: string }) =>
  decideStrategy({ method: 'GET', origin: ORIGIN, url: over.url ?? `${ORIGIN}${over.path ?? '/'}`, ...over });

describe('sw.js: roteamento de cache (allowlist)', () => {
  it('estáticos do app: cache-first', () => {
    for (const path of ['/_next/static/chunks/main-abc.js', '/manifest.json', '/logo.svg', '/icon-192.png', '/icons/maskable-512.png']) {
      expect(decide({ path }), path).toBe(STRATEGY.STATIC);
    }
  });

  it('navegação HTML: network-first', () => {
    expect(decide({ path: '/dashboard/', mode: 'navigate' })).toBe(STRATEGY.NAVIGATION);
    expect(decide({ path: '/', mode: 'navigate' })).toBe(STRATEGY.NAVIGATION);
    // navegações do navegador sempre vêm com credentials 'include'
    expect(decide({ path: '/dashboard/', mode: 'navigate', credentials: 'include' })).toBe(STRATEGY.NAVIGATION);
    expect(decide({ path: '/auth/me', mode: 'navigate', credentials: 'include' })).toBe(STRATEGY.BYPASS);
  });

  it('imagens de cards/equipamentos/academias: stale-while-revalidate', () => {
    expect(decide({ path: '/exercise-cards/1/v2/card.webp', destination: 'image' })).toBe(STRATEGY.IMAGE);
    expect(decide({ path: '/equipment/3/foto.jpg' })).toBe(STRATEGY.IMAGE);
    expect(decide({ path: '/gyms/9/logo.png' })).toBe(STRATEGY.IMAGE);
    // mesmo prefixo, mas não é imagem (rota/JSON): não entra
    expect(decide({ path: '/equipment/3/photo-data', destination: 'fetch' })).toBe(STRATEGY.BYPASS);
  });

  it('NUNCA cacheia a API (outra origem), nem com caminho de estático', () => {
    expect(decide({ url: 'https://api.musclefit.com.br/activities' })).toBe(STRATEGY.BYPASS);
    expect(decide({ url: 'https://api.musclefit.com.br/_next/static/x.js' })).toBe(STRATEGY.BYPASS);
    expect(decide({ url: 'http://mock-api.test/auth/me' })).toBe(STRATEGY.BYPASS);
    expect(decide({ url: 'https://api.musclefit.com.br/exercise-cards/1/card.webp', destination: 'image' })).toBe(STRATEGY.BYPASS);
  });

  it('NUNCA cacheia /auth/* e /api/* mesmo na mesma origem, inclusive navegação', () => {
    for (const path of ['/auth/me', '/auth/login', '/auth', '/api/x']) {
      expect(decide({ path, mode: 'navigate' }), path).toBe(STRATEGY.BYPASS);
      expect(decide({ path }), path).toBe(STRATEGY.BYPASS);
    }
  });

  it('não-GET, Authorization, credentials include e Range passam direto', () => {
    expect(decide({ path: '/_next/static/a.js', method: 'POST' })).toBe(STRATEGY.BYPASS);
    expect(decide({ path: '/_next/static/a.js', method: 'PATCH' })).toBe(STRATEGY.BYPASS);
    expect(decide({ path: '/_next/static/a.js', hasAuthorization: true })).toBe(STRATEGY.BYPASS);
    expect(decide({ path: '/_next/static/a.js', credentials: 'include', mode: 'cors' })).toBe(STRATEGY.BYPASS);
    // <script>/<link> sem crossorigin (no-cors) têm credentials 'include' por padrão: são estáticos públicos
    expect(decide({ path: '/_next/static/a.js', credentials: 'include', mode: 'no-cors' })).toBe(STRATEGY.STATIC);
    expect(decide({ path: '/gyms/1/a.png', hasRange: true })).toBe(STRATEGY.BYPASS);
  });

  it('o que não está na allowlist passa direto (RSC payloads, scripts de terceiros, URL inválida)', () => {
    expect(decide({ path: '/dashboard/index.txt' })).toBe(STRATEGY.BYPASS);
    expect(decide({ url: 'https://challenges.cloudflare.com/turnstile/v0/api.js' })).toBe(STRATEGY.BYPASS);
    expect(decide({ url: 'não é url' })).toBe(STRATEGY.BYPASS);
  });

  it('só respostas 200, sem redirect/opaque/no-store entram no cache', () => {
    const r = (o: Record<string, unknown>, cc = '') => ({
      status: 200,
      type: 'basic',
      redirected: false,
      headers: { get: () => cc },
      ...o,
    });
    expect(isCacheable(r({}))).toBe(true);
    expect(isCacheable(r({ status: 404 }))).toBe(false);
    expect(isCacheable(r({ type: 'opaque' }))).toBe(false);
    expect(isCacheable(r({ redirected: true }))).toBe(false);
    expect(isCacheable(r({}, 'no-store'))).toBe(false);
    expect(isCacheable(r({}, 'private, max-age=0'))).toBe(false);
  });
});

describe('sw.js: pré-cache do shell', () => {
  it('extrai só /_next/static/** (js/css/fonte), sem duplicar', () => {
    const html =
      '<script src="/_next/static/chunks/main-a1.js" async></script><link href="/_next/static/css/x.css">' +
      '<script>self.__next_f.push([1,"\\"/_next/static/chunks/main-a1.js\\" /_next/static/media/f.woff2"])</script>' +
      '<script src="https://cdn.exemplo.com/_next/static/y.js"></script><a href="/dashboard/">';
    const urls = extractStaticUrls(html);
    expect(urls).toContain('/_next/static/chunks/main-a1.js');
    expect(urls).toContain('/_next/static/css/x.css');
    expect(urls).toContain('/_next/static/media/f.woff2');
    expect(urls.filter((u) => u.endsWith('main-a1.js'))).toHaveLength(1);
    expect(urls.every((u) => u.startsWith('/_next/static/'))).toBe(true);
  });
});

describe('registro e atualização', () => {
  const base = { isProduction: true, mobileUiEnabled: true, supported: true };
  it('registra só em produção, com a flag ligada e com suporte', () => {
    expect(shouldRegisterServiceWorker(base)).toBe(true);
    expect(shouldRegisterServiceWorker({ ...base, isProduction: false })).toBe(false);
    expect(shouldRegisterServiceWorker({ ...base, mobileUiEnabled: false })).toBe(false);
    expect(shouldRegisterServiceWorker({ ...base, supported: false })).toBe(false);
  });
  it('flag desligada em produção desfaz o SW anterior; em dev nunca mexe', () => {
    expect(shouldUnregisterServiceWorker({ ...base, mobileUiEnabled: false })).toBe(true);
    expect(shouldUnregisterServiceWorker(base)).toBe(false);
    expect(shouldUnregisterServiceWorker({ ...base, mobileUiEnabled: false, isProduction: false })).toBe(false);
  });
  it('só oferece atualização com versão em waiting, sem trabalho ativo e fora do treino', () => {
    expect(shouldOfferUpdate({ waitingWorker: true, activeWork: false, silentPath: false })).toBe(true);
    expect(shouldOfferUpdate({ waitingWorker: true, activeWork: true, silentPath: false })).toBe(false);
    expect(shouldOfferUpdate({ waitingWorker: false, activeWork: false, silentPath: false })).toBe(false);
    expect(shouldOfferUpdate({ waitingWorker: true, activeWork: false, silentPath: true })).toBe(false);
  });
});

describe('update-gate: atualização com sessão ativa', () => {
  const make = (busy: boolean) => {
    const calls: string[] = [];
    return {
      calls,
      deps: {
        hasActiveWork: async () => busy,
        markApplying: () => calls.push('applying'),
        postSkipWaiting: () => calls.push('skip'),
      },
    };
  };
  it('com trabalho ativo NÃO envia SKIP_WAITING nem marca recarga', async () => {
    const t = make(true);
    expect(await tryApplyUpdate(t.deps)).toBe(false);
    expect(t.calls).toEqual([]);
  });
  it('ocioso: marca e envia SKIP_WAITING', async () => {
    const t = make(false);
    expect(await tryApplyUpdate(t.deps)).toBe(true);
    expect(t.calls).toEqual(['applying', 'skip']);
  });
  it('falha do provedor (hasActiveWork rejeita => true no contrato) também bloqueia', async () => {
    const calls: string[] = [];
    const ok = await tryApplyUpdate({
      hasActiveWork: async () => true,
      markApplying: () => calls.push('x'),
      postSkipWaiting: () => calls.push('y'),
    });
    expect(ok).toBe(false);
    expect(calls).toEqual([]);
  });
});
