import { execFileSync, spawn, type ChildProcess } from 'node:child_process';
import { copyFileSync } from 'node:fs';
import path from 'node:path';

import { expect, test, type Page } from '@playwright/test';


const FRONTEND_ROOT = path.resolve(__dirname, '../..');

/** Carimba/derruba o out/sw.js com o MESMO script que o deploy usa. */
function buildSw(args: string[], version?: string) {
  execFileSync(process.execPath, ['scripts/build-sw.mjs', ...args], {
    cwd: FRONTEND_ROOT,
    env: { ...process.env, ...(version ? { CACHE_VERSION: version } : {}) },
    stdio: 'pipe',
  });
}

async function waitControlled(page: Page) {
  await page.evaluate(() => navigator.serviceWorker.ready.then(() => undefined));
  await page.waitForFunction(() => navigator.serviceWorker.controller !== null, undefined, { timeout: 20_000 });
}

const cacheNames = (page: Page) => page.evaluate(() => caches.keys());
const registrations = (page: Page) => page.evaluate(() => navigator.serviceWorker.getRegistrations().then((r) => r.length));

test.describe.configure({ mode: 'serial' });

/** Volta o out/sw.js ao original de public/ (o teste de kill o sobrescreve) e carimba a versão. */
function resetSw(version: string) {
  copyFileSync(path.join(FRONTEND_ROOT, 'public/sw.js'), path.join(FRONTEND_ROOT, 'out/sw.js'));
  buildSw([], version);
}

test.beforeAll(() => resetSw('e2e-v1'));
test.afterAll(() => resetSw('dev'));

test('flag desligada: o SW não registra', async ({ page }) => {
  await page.goto('/?mobile=0');
  await page.waitForLoadState('networkidle');
  expect(await registrations(page)).toBe(0);
  expect(await cacheNames(page)).toEqual([]);
});

test('flag ligada e depois desligada: desregistra e apaga os caches musclefit-*', async ({ page }) => {
  await page.goto('/?mobile=1');
  await waitControlled(page);
  expect(await registrations(page)).toBe(1);
  expect((await cacheNames(page)).length).toBeGreaterThan(0);

  await page.goto('/?mobile=0');
  await expect.poll(() => registrations(page), { timeout: 20_000 }).toBe(0);
  await expect.poll(async () => (await cacheNames(page)).filter((n) => n.startsWith('musclefit-')), { timeout: 20_000 }).toEqual([]);
});

test('flag ligada: registra com escopo "/" e pré-cacheia o shell com seus chunks', async ({ page }) => {
  await page.goto('/?mobile=1');
  await waitControlled(page);
  const scope = await page.evaluate(() => navigator.serviceWorker.getRegistration().then((r) => r?.scope));
  expect(scope).toBe(`${new URL(page.url()).origin}/`);

  const names = await cacheNames(page);
  expect(names.sort()).toEqual(expect.arrayContaining(['musclefit-pages-e2e-v1', 'musclefit-static-e2e-v1']));
  const staticUrls = await page.evaluate(async () => {
    const c = await caches.open('musclefit-static-e2e-v1');
    return (await c.keys()).map((r) => new URL(r.url).pathname);
  });
  expect(staticUrls).toContain('/manifest.json');
  expect(staticUrls.some((u) => u.startsWith('/_next/static/'))).toBe(true);
});

// Offline REAL: o Chromium com `setOffline` derruba a navegação antes de o SW
// responder (diferente de um aparelho de verdade), então o teste usa um servidor
// próprio e o encerra. A rede falha como falharia no celular (conexão recusada).
const OFFLINE_PORT = Number(process.env.E2E_PORT ?? 4174) + 2;
const OFFLINE_ORIGIN = `http://127.0.0.1:${OFFLINE_PORT}`;

async function startStaticServer(): Promise<ChildProcess> {
  const child = spawn(process.execPath, ['scripts/serve-export.mjs', '--port', String(OFFLINE_PORT), '--dir', 'out'], {
    cwd: FRONTEND_ROOT,
    stdio: 'ignore',
  });
  for (let i = 0; i < 50; i += 1) {
    try {
      if ((await fetch(`${OFFLINE_ORIGIN}/manifest.json`)).ok) return child;
    } catch {
      // ainda subindo
    }
    await new Promise((r) => setTimeout(r, 200));
  }
  child.kill();
  throw new Error('servidor estático não subiu');
}

test('offline: shell abre do cache (HTML + chunks) e rota nunca visitada cai no shell', async ({ page }) => {
  const server = await startStaticServer();
  try {
    await page.goto(`${OFFLINE_ORIGIN}/?mobile=1`);
    await waitControlled(page);
    await page.reload();
    await page.waitForLoadState('networkidle');
  } finally {
    server.kill();
  }
  await new Promise((r) => setTimeout(r, 500));

  // Servidor morto: tudo que carregar veio do Cache Storage.
  await page.goto(`${OFFLINE_ORIGIN}/?mobile=1`);
  await expect(page.getByText(/entrar/i).first()).toBeVisible({ timeout: 20_000 });

  const res = await page.goto(`${OFFLINE_ORIGIN}/perfil/`);
  expect(res?.status()).toBe(200); // fallback ao shell, não erro do navegador
  // O corpo da resposta é o HTML do shell em cache (o app em /perfil pode não ter o chunk da rota offline).
  expect(await res!.text()).toContain('<title>MuscleFit</title>');
});

test('nenhuma resposta da API (nem /auth) vai para o Cache Storage, mesmo com credentials e Authorization', async ({ page }) => {
  await page.goto('/?mobile=1');
  await waitControlled(page);
  await page.reload();
  await expect(page.getByText(/entrar/i).first()).toBeVisible({ timeout: 20_000 });
  await page.waitForLoadState('networkidle');

  // O app já chamou GET /auth/me (401). Reforça com GET autenticado, POST e Authorization.
  await page.evaluate(async () => {
    const api = 'http://mock-api.test';
    await fetch(`${api}/activities`, { credentials: 'include' });
    await fetch(`${api}/workout-sessions`, { headers: { Authorization: 'Bearer x' } });
    await fetch(`${api}/auth/logout`, { method: 'POST', credentials: 'include' });
    await fetch('/auth/me', { credentials: 'include' }).catch(() => undefined);
  });

  const urls = await page.evaluate(async () => {
    const out: string[] = [];
    for (const name of await caches.keys()) {
      for (const req of await (await caches.open(name)).keys()) out.push(req.url);
    }
    return out;
  });
  expect(urls.length).toBeGreaterThan(0);
  expect(urls.filter((u) => /mock-api\.test|api\.musclefit|\/auth\/|\/activities|\/workout-sessions/.test(u))).toEqual([]);
});

test('atualização sem provedor de trabalho ativo: fica em waiting, sem aviso e sem recarregar', async ({ page }) => {
  await page.goto('/?mobile=1');
  await waitControlled(page);
  await page.evaluate(() => {
    (window as unknown as { __marker: string }).__marker = 'viva';
  });

  buildSw([], 'e2e-v2');
  await page.evaluate(() => navigator.serviceWorker.getRegistration().then((r) => r?.update()));
  await expect
    .poll(() => page.evaluate(() => navigator.serviceWorker.getRegistration().then((r) => !!r?.waiting)), { timeout: 20_000 })
    .toBe(true);

  // Nenhuma frente registrou provedor de hasActiveWork(): "desconhecido" = ocupado. Sem aviso
  // (esperando mais que o intervalo de reavaliação de 5 s), sem recarga, SW antigo ativo.
  await page.waitForTimeout(7000);
  await expect(page.getByTestId('pwa-update-notice')).toHaveCount(0);
  expect(await page.evaluate(() => (window as unknown as { __marker?: string }).__marker)).toBe('viva');
  const during = await cacheNames(page);
  expect(during).toEqual(expect.arrayContaining(['musclefit-static-e2e-v1', 'musclefit-static-e2e-v2']));

  // Lado do SW: a mensagem SKIP_WAITING (que a página só manda sem trabalho ativo) ativa a versão
  // nova e troca os caches. A página, sem ter pedido, NÃO recarrega.
  await page.evaluate(() => navigator.serviceWorker.getRegistration().then((r) => r?.waiting?.postMessage({ type: 'SKIP_WAITING' })));
  await expect.poll(() => cacheNames(page), { timeout: 20_000 }).not.toContain('musclefit-static-e2e-v1');
  expect(await page.evaluate(() => (window as unknown as { __marker?: string }).__marker)).toBe('viva');
});

test('kill switch: o sw.js autodestrutivo desregistra e apaga os caches sem recarregar a aba', async ({ page }) => {
  await page.goto('/?mobile=1');
  await waitControlled(page);
  await page.evaluate(() => {
    (window as unknown as { __marker: string }).__marker = 'viva';
  });

  buildSw(['--kill']);
  await page.evaluate(() => navigator.serviceWorker.getRegistration().then((r) => r?.update()));

  await expect.poll(() => registrations(page), { timeout: 20_000 }).toBe(0);
  await expect.poll(() => cacheNames(page), { timeout: 20_000 }).toEqual([]);
  expect(await page.evaluate(() => (window as unknown as { __marker?: string }).__marker)).toBe('viva');
});

test('rota de autenticação com token na URL nunca vira chave do Cache Storage', async ({ page }) => {
  resetSw('e2e-v1'); // o teste anterior (kill switch) deixa o sw.js autodestrutivo em out/
  await page.goto('/?mobile=1');
  await waitControlled(page);
  await page.goto('/redefinir-senha/?token=segredo-123&mobile=1');
  await page.goto('/perfil/?mobile=1&x=1');
  await page.waitForLoadState('networkidle');
  const urls = await page.evaluate(async () => {
    const out: string[] = [];
    for (const name of await caches.keys()) {
      for (const req of await (await caches.open(name)).keys()) out.push(req.url);
    }
    return out;
  });
  // Chunks JS públicos (/_next/static/**) podem citar a rota; o que não pode existir é página dela ou query.
  expect(urls.filter((u) => !u.includes('/_next/static/') && /redefinir|esqueci|token|segredo|[?]/.test(u))).toEqual([]);
  expect(urls.filter((u) => u.includes('?'))).toEqual([]);
});
