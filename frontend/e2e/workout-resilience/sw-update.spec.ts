import { execFileSync } from 'node:child_process';
import { copyFileSync } from 'node:fs';
import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';
import { API, installWorkoutApi, SESSION_ID } from './mock-workout-api';

/**
 * Atualizacao do service worker x trabalho ativo (provedores de hasActiveWork da
 * Frente B). Mesmo padrao de e2e/pwa/service-worker.spec.ts: out/sw.js e carimbado
 * com o script de build real e a versao nova entra em `waiting`.
 */
const ROOT = path.resolve(__dirname, '../..');

function buildSw(version: string) {
  execFileSync(process.execPath, ['scripts/build-sw.mjs'], {
    cwd: ROOT,
    env: { ...process.env, CACHE_VERSION: version },
    stdio: 'pipe',
  });
}
function resetSw(version: string) {
  copyFileSync(path.join(ROOT, 'public/sw.js'), path.join(ROOT, 'out/sw.js'));
  buildSw(version);
}

async function waitControlled(page: Page) {
  await page.evaluate(() => navigator.serviceWorker.ready.then(() => undefined));
  await page.waitForFunction(() => navigator.serviceWorker.controller !== null, undefined, { timeout: 20_000 });
}

const hasWaiting = (page: Page) =>
  page.evaluate(() => navigator.serviceWorker.getRegistration().then((r) => !!r?.waiting));

test.describe.configure({ mode: 'serial' });
test.beforeAll(() => resetSw('e2e-w1'));
test.afterAll(() => resetSw('dev'));

async function anonymousLogin(page: Page) {
  await installWorkoutApi(page);
  // Pagina de login (401): o SW registra e nada de sessao autenticada entra em cena.
  await page.route(`${API}/auth/me`, (route) =>
    route.fulfill({
      status: 401,
      headers: { 'access-control-allow-origin': route.request().headers()['origin'] ?? '*', 'access-control-allow-credentials': 'true', 'content-type': 'application/json' },
      body: '{"detail":"Nao autenticado"}',
    }),
  );
}

test('sem trabalho ativo: avisa "Atualização disponível" e, ao tocar em Atualizar, aplica a versao nova e recarrega', async ({ page }) => {
  resetSw('e2e-w1');
  await anonymousLogin(page);
  await page.goto('/?mobile=1');
  await waitControlled(page);
  await page.evaluate(() => {
    (window as unknown as { __marker: string }).__marker = 'viva';
  });

  buildSw('e2e-w2');
  await page.evaluate(() => navigator.serviceWorker.getRegistration().then((r) => r?.update()));
  await expect.poll(() => hasWaiting(page), { timeout: 20_000 }).toBe(true);

  await expect(page.getByTestId('pwa-update-notice')).toBeVisible({ timeout: 20_000 });
  await expect(page.getByTestId('pwa-update-notice')).toContainText('Atualização disponível');

  await Promise.all([page.waitForEvent('load'), page.getByRole('button', { name: 'Atualizar' }).click()]);
  expect(await page.evaluate(() => (window as unknown as { __marker?: string }).__marker)).toBeUndefined(); // recarregou
  await waitControlled(page);
  await expect
    .poll(() => page.evaluate(() => caches.keys()), { timeout: 20_000 })
    .not.toContain('musclefit-static-e2e-w1');
});

test('com sessao de treino ativa neste aparelho: sem aviso, sem recarregar, versao nova segue em waiting', async ({ page }) => {
  resetSw('e2e-w3');
  await anonymousLogin(page);
  // Sessao local em andamento do usuario 1 (mesmo formato gravado pelo app).
  await page.addInitScript((sessionId) => {
    window.localStorage.setItem('musclefit.offline.active-user', '1');
    const open = indexedDB.open('musclefit', 1);
    open.onupgradeneeded = () => {
      for (const name of ['workout_session', 'op_queue', 'meta']) open.result.createObjectStore(name);
    };
    open.onsuccess = () => {
      const tx = open.result.transaction('workout_session', 'readwrite');
      tx.objectStore('workout_session').put({ session_id: sessionId, source: 'plan', workout_plan_id: 50 }, 'v1:1:session');
    };
  }, SESSION_ID);
  await page.goto('/?mobile=1');
  await waitControlled(page);
  await page.evaluate(() => {
    (window as unknown as { __marker: string }).__marker = 'viva';
  });

  buildSw('e2e-w4');
  await page.evaluate(() => navigator.serviceWorker.getRegistration().then((r) => r?.update()));
  await expect.poll(() => hasWaiting(page), { timeout: 20_000 }).toBe(true);

  await page.waitForTimeout(8000); // mais que o intervalo de reavaliacao
  await expect(page.getByTestId('pwa-update-notice')).toHaveCount(0);
  expect(await page.evaluate(() => (window as unknown as { __marker?: string }).__marker)).toBe('viva');
  expect(await hasWaiting(page)).toBe(true);
});
