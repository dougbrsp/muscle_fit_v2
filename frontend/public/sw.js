/* MuscleFit service worker (plano mobile 3.7). Escrito a mão, sem Workbox.
 *
 * O QUE FAZ
 *  - Estáticos do app (/_next/static/**, manifest, ícones, logo): cache-first.
 *  - Navegação HTML: network-first (com tempo limite) e fallback para a página
 *    em cache ou para o shell "/" quando offline.
 *  - Imagens de cards/equipamentos/academias: stale-while-revalidate com teto.
 *
 * O QUE NUNCA FAZ (allowlist, não denylist)
 *  - Só trata GET da MESMA origem do app e só os caminhos listados abaixo. A API
 *    (outra origem: api.musclefit.com.br / NEXT_PUBLIC_API_BASE_URL), /auth/*,
 *    requisições com Authorization ou credentials 'include', Range e qualquer
 *    método diferente de GET passam direto para a rede (sem respondWith).
 *  - Por construção não há dado autenticado no Cache Storage: o HTML é estático
 *    (export do Next, sem dado de usuário) e as imagens/estáticos são públicos.
 *    Por isso o logout não precisa limpar estes caches (nem um 401).
 *
 * ATUALIZAÇÃO
 *  - Sem skipWaiting automático: a nova versão fica em `waiting` até a página
 *    mandar { type: 'SKIP_WAITING' } (só quando não há treino/ops pendentes).
 *
 * KILL SWITCH / ROLLBACK
 *  - `node scripts/build-sw.mjs --kill` troca o `sw.js` publicado por uma versão
 *    autodestrutiva (apaga os caches musclefit-* e se desregistra, sem recarregar
 *    nenhuma aba). Com a flag mobile desligada o app também desregistra o SW.
 *
 * `CACHE_VERSION` é carimbado no build por `scripts/build-sw.mjs`.
 */
'use strict';

const CACHE_VERSION = 'dev';

const CACHE_PREFIX = 'musclefit-';
const STATIC_CACHE = `${CACHE_PREFIX}static-${CACHE_VERSION}`;
const PAGES_CACHE = `${CACHE_PREFIX}pages-${CACHE_VERSION}`;
const IMAGES_CACHE = `${CACHE_PREFIX}images-${CACHE_VERSION}`;
const CURRENT_CACHES = [STATIC_CACHE, PAGES_CACHE, IMAGES_CACHE];

const SHELL_URL = '/';
const PRECACHE_STATIC = [
  '/manifest.json',
  '/logo.svg',
  '/icon.svg',
  '/icon-192.png',
  '/icon-512.png',
  '/apple-touch-icon.png',
  '/icons/maskable-192.png',
  '/icons/maskable-512.png',
];

const NAVIGATION_NETWORK_TIMEOUT_MS = 4000;
const MAX_STATIC_ENTRIES = 400;
const MAX_PAGE_ENTRIES = 40;
const MAX_IMAGE_ENTRIES = 200;
const MAX_SHELL_STATIC_PRECACHE = 80;

const STATIC_PREFIXES = ['/_next/static/', '/icons/', '/bodymap/'];
const STATIC_FILES = [
  '/manifest.json',
  '/logo.svg',
  '/icon.svg',
  '/icon-192.png',
  '/icon-512.png',
  '/apple-touch-icon.png',
];
const IMAGE_PREFIXES = ['/exercise-cards/', '/equipment/', '/gyms/'];
const IMAGE_EXTENSION = /\.(png|jpe?g|webp|avif|gif|svg)$/i;
// Caminhos que jamais passam pelo cache, mesmo que casassem com a allowlist.
const NEVER_CACHE_PREFIXES = ['/auth/', '/api/'];

const STRATEGY = {
  BYPASS: 'bypass',
  STATIC: 'cache-first',
  NAVIGATION: 'network-first',
  IMAGE: 'stale-while-revalidate',
};

/**
 * Decisão pura de roteamento de cache (testada em src/lib/pwa/sw-policy.test.ts).
 * input: { method, url, origin, mode, destination, credentials, hasAuthorization, hasRange }
 */
function decideStrategy(input) {
  if (input.method !== 'GET') return STRATEGY.BYPASS;
  if (input.hasAuthorization || input.hasRange) return STRATEGY.BYPASS;
  // fetch()/XHR com credentials 'include' (mode 'cors') é chamada de API: nunca. Navegações e
  // <script>/<link>/<img> sem crossorigin também carregam credentials 'include' por padrão do
  // navegador, mas são cargas passivas de arquivos estáticos públicos (e só passam pela allowlist).
  if (input.credentials === 'include' && input.mode === 'cors') return STRATEGY.BYPASS;

  let url;
  try {
    url = new URL(input.url);
  } catch (e) {
    return STRATEGY.BYPASS;
  }
  // Outra origem (API, CDN de terceiros, Turnstile...): nunca.
  if (url.origin !== input.origin) return STRATEGY.BYPASS;

  const path = url.pathname;
  if (NEVER_CACHE_PREFIXES.some((p) => path === p.slice(0, -1) || path.startsWith(p))) return STRATEGY.BYPASS;

  if (input.mode === 'navigate') return STRATEGY.NAVIGATION;

  if (STATIC_FILES.includes(path) || STATIC_PREFIXES.some((p) => path.startsWith(p))) return STRATEGY.STATIC;

  if (IMAGE_PREFIXES.some((p) => path.startsWith(p)) && (input.destination === 'image' || IMAGE_EXTENSION.test(path))) {
    return STRATEGY.IMAGE;
  }
  return STRATEGY.BYPASS;
}

/** Pura: URLs de /_next/static/** citadas no HTML do shell (src/href e payload do Next). */
function extractStaticUrls(html) {
  const found = html.match(/\/_next\/static\/[A-Za-z0-9_\-./%~@]+\.(?:js|css|woff2?)/g) || [];
  return Array.from(new Set(found)).slice(0, MAX_SHELL_STATIC_PRECACHE);
}

/** Só respostas completas, sem redirecionamento e sem `no-store` entram no cache. */
function isCacheable(response) {
  if (!response || response.status !== 200 || response.type === 'opaque' || response.redirected) return false;
  const cc = response.headers.get('Cache-Control') || '';
  return !/no-store|private/i.test(cc);
}

async function trimCache(cacheName, max) {
  const cache = await caches.open(cacheName);
  const keys = await cache.keys();
  for (let i = 0; i < keys.length - max; i += 1) await cache.delete(keys[i]);
}

async function putAndTrim(cacheName, request, response, max) {
  const cache = await caches.open(cacheName);
  await cache.put(request, response);
  await trimCache(cacheName, max);
}

async function cacheFirst(request) {
  const cached = await caches.match(request, { cacheName: STATIC_CACHE });
  if (cached) return cached;
  const response = await fetch(request);
  if (isCacheable(response)) {
    const copy = response.clone();
    await putAndTrim(STATIC_CACHE, request, copy, MAX_STATIC_ENTRIES);
  }
  return response;
}

function fetchWithTimeout(request, ms) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('timeout')), ms);
    fetch(request).then(
      (r) => {
        clearTimeout(timer);
        resolve(r);
      },
      (e) => {
        clearTimeout(timer);
        reject(e);
      },
    );
  });
}

async function networkFirstNavigation(request) {
  try {
    const response = await fetchWithTimeout(request, NAVIGATION_NETWORK_TIMEOUT_MS);
    if (isCacheable(response)) {
      const copy = response.clone();
      await putAndTrim(PAGES_CACHE, request, copy, MAX_PAGE_ENTRIES);
    }
    return response;
  } catch (e) {
    const cached =
      (await caches.match(request, { cacheName: PAGES_CACHE, ignoreSearch: true })) ||
      (await caches.match(SHELL_URL, { cacheName: PAGES_CACHE }));
    if (cached) return cached;
    return new Response('Sem conexão. Abra o MuscleFit novamente quando estiver online.', {
      status: 503,
      headers: { 'Content-Type': 'text/plain; charset=utf-8' },
    });
  }
}

async function staleWhileRevalidate(request, event) {
  const cache = await caches.open(IMAGES_CACHE);
  const cached = await cache.match(request);
  const refresh = fetch(request)
    .then(async (response) => {
      if (isCacheable(response)) await putAndTrim(IMAGES_CACHE, request, response.clone(), MAX_IMAGE_ENTRIES);
      return response;
    })
    .catch(() => null);
  if (cached) {
    event.waitUntil(refresh);
    return cached;
  }
  const fresh = await refresh;
  return fresh || Response.error();
}

self.addEventListener('install', (event) => {
  event.waitUntil(
    (async () => {
      const statics = await caches.open(STATIC_CACHE);
      const pages = await caches.open(PAGES_CACHE);
      // Falha de um item não pode abortar a instalação: o app funciona sem precache.
      await Promise.allSettled([
        ...PRECACHE_STATIC.map(async (u) => {
          const r = await fetch(new Request(u, { cache: 'reload' }));
          if (isCacheable(r)) await statics.put(u, r);
        }),
        (async () => {
          const r = await fetch(new Request(SHELL_URL, { cache: 'reload' }));
          if (!isCacheable(r)) return;
          // Os chunks do shell já foram pedidos pela página ANTES de o SW controlá-la;
          // sem pré-cache deles o shell em cache abriria sem JS no 1º uso offline.
          const html = await r.clone().text();
          await pages.put(SHELL_URL, r);
          await Promise.allSettled(
            extractStaticUrls(html).map(async (u) => {
              const a = await fetch(u);
              if (isCacheable(a)) await statics.put(u, a);
            }),
          );
        })(),
      ]);
      // Sem skipWaiting aqui: a página decide quando aplicar (mensagem SKIP_WAITING).
    })(),
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    (async () => {
      const names = await caches.keys();
      await Promise.all(
        names.filter((n) => n.startsWith(CACHE_PREFIX) && !CURRENT_CACHES.includes(n)).map((n) => caches.delete(n)),
      );
      await self.clients.claim();
    })(),
  );
});

self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') self.skipWaiting();
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  const strategy = decideStrategy({
    method: req.method,
    url: req.url,
    origin: self.location.origin,
    mode: req.mode,
    destination: req.destination,
    credentials: req.credentials,
    hasAuthorization: req.headers.has('Authorization'),
    hasRange: req.headers.has('Range'),
  });
  if (strategy === STRATEGY.BYPASS) return; // sem respondWith: a rede trata normalmente
  if (strategy === STRATEGY.STATIC) event.respondWith(cacheFirst(req));
  else if (strategy === STRATEGY.NAVIGATION) event.respondWith(networkFirstNavigation(req));
  else event.respondWith(staleWhileRevalidate(req, event));
});

// Exposto só para teste unitário (não altera o comportamento do worker).
self.__musclefitSwPolicy = { decideStrategy, STRATEGY, isCacheable, extractStaticUrls, CACHE_VERSION, CACHE_PREFIX };
