import { expect, test, type Page } from '@playwright/test';
import { installWorkoutApi, LEGACY_SESSION, type MockWorkoutApi } from './mock-workout-api';

/**
 * Resiliencia do Modo Treino em 390x844 (API mockada com estado). Cada teste
 * parte de uma sessao ja em andamento (chave legada do localStorage, que
 * tambem exercita a migracao para o IndexedDB do usuario).
 */

async function seedLegacySession(page: Page) {
  await page.addInitScript((session) => {
    if (!window.localStorage.getItem('e2e-seeded')) {
      window.localStorage.setItem('musclefit.workout-mode-session', JSON.stringify(session));
      window.localStorage.setItem('e2e-seeded', '1');
    }
  }, LEGACY_SESSION);
}

async function openWorkout(page: Page) {
  await page.goto('/modo-treino/');
  const start = page.getByRole('button', { name: 'Iniciar exercício' });
  await expect(start.or(page.getByTestId('set-save'))).toBeVisible({ timeout: 20_000 });
  if (await start.isVisible()) await start.click();
  await expect(page.getByTestId('set-save')).toBeVisible();
}

async function logSet(page: Page, reps: number, weight: number, rpe = 8) {
  const inputs = page.locator('input[inputmode="numeric"]');
  await inputs.nth(0).fill(String(reps));
  await inputs.nth(1).fill(String(weight));
  await inputs.nth(2).fill(String(rpe));
  await page.getByTestId('set-save').click();
}

/** Chaves do banco `musclefit` (todas as stores) lidas direto do IndexedDB da pagina. */
async function idbKeys(page: Page): Promise<string[]> {
  return page.evaluate(
    () =>
      new Promise<string[]>((resolve, reject) => {
        const open = indexedDB.open('musclefit');
        open.onerror = () => reject(open.error);
        open.onsuccess = () => {
          const db = open.result;
          const names = Array.from(db.objectStoreNames);
          const keys: string[] = [];
          let pending = names.length;
          if (pending === 0) {
            resolve(keys);
            return;
          }
          for (const name of names) {
            const req = db.transaction(name, 'readonly').objectStore(name).getAllKeys();
            req.onsuccess = () => {
              keys.push(...req.result.map((k) => `${name}/${String(k)}`));
              pending -= 1;
              if (pending === 0) resolve(keys);
            };
            req.onerror = () => reject(req.error);
          }
        };
      }),
  );
}

let api: MockWorkoutApi;

test.beforeEach(async ({ page }) => {
  api = await installWorkoutApi(page);
  await seedLegacySession(page);
});

test('migra a sessao legada para o IndexedDB do usuario e remove a chave antiga', async ({ page }) => {
  await openWorkout(page);
  const keys = await idbKeys(page);
  expect(keys.some((k) => k.startsWith('workout_session/v1:1:session'))).toBe(true);
  expect(await page.evaluate(() => window.localStorage.getItem('musclefit.workout-mode-session'))).toBeNull();
});

test('registra serie, descanso, vai ao segundo plano e volta com dados integros e cronometro correto', async ({ page }) => {
  await page.clock.install();
  await openWorkout(page);
  await logSet(page, 10, 40);
  await expect(page.getByTestId('rest-clock')).toBeVisible();
  expect(api.serverSets()).toHaveLength(1);

  // Segundo plano: o relogio pula 25 s sem nenhum tick; ao voltar recalcula pelo timestamp absoluto.
  await page.clock.fastForward(25_000);
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
  const text = await page.getByTestId('rest-clock').innerText();
  const [mm, ss] = text.split(':').map(Number);
  expect(mm * 60 + ss).toBeGreaterThan(0);
  expect(mm * 60 + ss).toBeLessThanOrEqual(36); // 60 s de descanso - ~25 s (tolerancia de execucao)
  expect(api.serverSets()).toHaveLength(1);

  // Passou do fim em segundo plano: o descanso encerra pelo relogio, sem timer zumbi nem serie duplicada.
  await page.clock.fastForward(60_000);
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
  await expect(page.getByTestId('set-save')).toBeVisible();
  expect(api.serverSets()).toHaveLength(1);
});

test('serie offline fica salva no aparelho e, ao reconectar, vai ao servidor exatamente uma vez com a mesma client_key', async ({
  page,
  context,
}) => {
  await openWorkout(page);
  await logSet(page, 10, 40);
  await expect(page.getByTestId('rest-clock')).toBeVisible();
  await page.getByRole('button', { name: /pular descanso/i }).click();
  await expect(page.getByTestId('set-save')).toBeVisible();

  api.setNetworkDown(true);
  await context.setOffline(true);
  await logSet(page, 9, 40);
  await expect(page.getByTestId('set-local-chip')).toContainText('salva no aparelho');
  await expect(page.getByTestId('sync-indicator')).toContainText('aguardando envio');
  await expect(page.getByText('Série sincronizada')).toHaveCount(0);
  expect(api.serverSets()).toHaveLength(1);

  api.setNetworkDown(false);
  await context.setOffline(false);
  await expect.poll(() => api.serverSets().length, { timeout: 30_000 }).toBe(2);
  await expect(page.getByTestId('sync-indicator')).toHaveCount(0);

  const postsByKey = new Map<string, number>();
  for (const post of api.setPosts.filter((p) => p.status === 200)) {
    postsByKey.set(post.client_key, (postsByKey.get(post.client_key) ?? 0) + 1);
  }
  expect(Array.from(postsByKey.values()).every((count) => count === 1)).toBe(true);
  expect(postsByKey.size).toBe(2);
});

test('fechar e reabrir a pagina com serie pendente restaura e sincroniza depois', async ({ page, context }) => {
  await openWorkout(page);
  api.failNextSetPosts(1000, 503);
  await logSet(page, 10, 40);
  await expect(page.getByTestId('sync-indicator')).toContainText('aguardando envio');
  expect(api.serverSets()).toHaveLength(0);

  await page.reload();
  await expect(page.getByTestId('sync-indicator')).toContainText('aguardando envio');
  const keys = await idbKeys(page);
  expect(keys.filter((k) => k.startsWith('op_queue/v1:1:op:'))).toHaveLength(1);

  api.failNextSetPosts(0);
  await context.setOffline(true);
  await context.setOffline(false); // dispara o evento online
  await expect.poll(() => api.serverSets().length, { timeout: 30_000 }).toBe(1);
  await expect(page.getByTestId('sync-indicator')).toHaveCount(0);
});

test('API 500 mostra erro recuperavel e nunca sincronizado; tentar de novo grava uma unica vez', async ({ page }) => {
  await openWorkout(page);
  api.failNextSetPosts(1, 500);
  await logSet(page, 10, 40);
  await expect(page.getByTestId('set-saved-chip')).toHaveCount(0);
  await expect(page.getByText('Série sincronizada')).toHaveCount(0);
  expect(api.serverSets()).toHaveLength(0);
  await expect(page.getByTestId('set-save')).toBeEnabled();

  await page.getByTestId('set-save').click();
  await expect.poll(() => api.serverSets().length).toBe(1);
  const keys = new Set(api.setPosts.map((p) => p.client_key));
  expect(keys.size).toBe(1); // a nova tentativa reaproveita a mesma client_key
});

test('girar o aparelho nao reinicia a sessao nem duplica a serie', async ({ page }) => {
  await openWorkout(page);
  await logSet(page, 10, 40);
  await expect(page.getByTestId('rest-clock')).toBeVisible();
  await page.setViewportSize({ width: 844, height: 390 });
  await expect(page.getByTestId('rest-clock')).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByTestId('rest-clock')).toBeVisible();
  expect(api.serverSets()).toHaveLength(1);
  expect(api.setPosts).toHaveLength(1);
  const keys = await idbKeys(page);
  expect(keys.filter((k) => k.startsWith('workout_session/v1:1:session'))).toHaveLength(1);
});

test('logout apaga os dados locais do usuario e outra conta nao ve a sessao anterior', async ({ page }) => {
  await openWorkout(page);
  expect((await idbKeys(page)).some((k) => k.includes('v1:1:'))).toBe(true);

  // Logout real pelo menu do AppShell.
  await page.goto('/dashboard/');
  await page.getByRole('banner').getByRole('button').last().click();
  await page.getByRole('menuitem', { name: 'Sair' }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect.poll(async () => (await idbKeys(page)).filter((k) => k.includes('v1:1:')).length).toBe(0);

  // Outra conta no mesmo aparelho: nada da anterior.
  api.loginAs('bia');
  await page.goto('/modo-treino/');
  await expect(page.getByText(/modo de treino/i).first()).toBeVisible({ timeout: 20_000 });
  await expect(page.getByTestId('set-save')).toHaveCount(0);
  const keys = await idbKeys(page);
  expect(keys.filter((k) => k.includes('v1:1:'))).toEqual([]);
});

test('conta diferente nao herda a sessao legada do aparelho (404 de posse descarta a chave)', async ({ page }) => {
  api.loginAs('bia');
  await page.goto('/modo-treino/');
  await expect(page.getByText(/modo de treino/i).first()).toBeVisible({ timeout: 20_000 });
  await expect(page.getByTestId('set-save')).toHaveCount(0);
  expect(await page.evaluate(() => window.localStorage.getItem('musclefit.workout-mode-session'))).toBeNull();
  expect((await idbKeys(page)).filter((k) => k.includes('session'))).toEqual([]);
});

test('FST-7: 7 series, reducao de carga em um toque durante o descanso e carga efetiva gravada, sem RPE manual', async ({ page }) => {
  await page.addInitScript((session) => {
    window.localStorage.setItem(
      'musclefit.workout-mode-session',
      JSON.stringify({ ...session, advanced_mode: true, cancelled_exercise_ids: [7001, 7002] }),
    );
  }, LEGACY_SESSION);
  await page.goto('/modo-treino/');
  await page.getByTestId('briefing-start').click();
  await page.getByTestId('intensive-start').click();

  for (let index = 1; index <= 7; index += 1) {
    await expect(page.getByTestId('intensive-set-label')).toContainText(`Série ${index} de 7`);
    await page.getByTestId('intensive-reps-input').fill('10');
    await page.getByTestId('intensive-complete-set').click();
    if (index === 7) break;
    await expect(page.getByTestId('rest-primary-cta')).toBeVisible();
    // Registro durante o descanso curto: reduz a carga das proximas series em um toque.
    if (index === 3) await page.getByTestId('quick-adjust-minus_step').click();
    await page.getByTestId('rest-primary-cta').click();
  }

  await expect.poll(() => api.fstBodies.length, { timeout: 20_000 }).toBe(7);
  const weights = api.fstBodies.map((body) => Number(body.weight_kg));
  expect(weights.slice(0, 3)).toEqual([20, 20, 20]);
  expect(weights[3]).toBeLessThan(20); // a reducao vale a partir da 4a
  expect(weights.slice(3).every((w) => w === weights[3])).toBe(true);
  for (const body of api.fstBodies) {
    expect(body.rpe).toBeNull(); // sem RPE manual por serie
    expect(body.rpe_target_source).toBe('protocol'); // meta do protocolo vem do servidor, nao do cliente
  }
  expect(new Set(api.fstBodies.map((body) => body.client_key)).size).toBe(7);
});

test('descanso do FST-7 vencido em segundo plano mostra "expirado ha mm:ss" ao voltar', async ({ page }) => {
  await page.clock.install();
  await page.addInitScript((session) => {
    window.localStorage.setItem(
      'musclefit.workout-mode-session',
      JSON.stringify({ ...session, advanced_mode: true, cancelled_exercise_ids: [7001, 7002] }),
    );
  }, LEGACY_SESSION);
  await page.goto('/modo-treino/');
  await page.getByTestId('briefing-start').click();
  await page.getByTestId('intensive-start').click();
  await page.getByTestId('intensive-reps-input').fill('10');
  await page.getByTestId('intensive-complete-set').click();
  await expect(page.getByTestId('rest-clock')).toBeVisible();

  await page.clock.fastForward(50_000); // descanso de 30 s venceu ha ~20 s
  await page.evaluate(() => window.dispatchEvent(new Event('pageshow')));
  await expect(page.getByTestId('rest-clock')).toHaveText('00:00');
  const overdue = await page.getByTestId('rest-overdue').innerText();
  const match = /expirado há (\d{2}):(\d{2})/i.exec(overdue);
  expect(match).not.toBeNull();
  const seconds = Number(match?.[1]) * 60 + Number(match?.[2]);
  expect(seconds).toBeGreaterThanOrEqual(15);
  expect(seconds).toBeLessThanOrEqual(25);
});

test('reabrir 100% offline restaura a tela do exercicio, aceita nova serie e sincroniza ao voltar a rede', async ({ page }) => {
  await openWorkout(page);
  await logSet(page, 10, 40);
  await expect(page.getByTestId('rest-clock')).toBeVisible();
  expect(api.serverSets()).toHaveLength(1);

  api.setNetworkDown(true);
  await page.reload();
  await expect(page.getByTestId('offline-banner')).toContainText('Offline');
  // Retrato do ultimo estado do servidor (IndexedDB do usuario) + descanso restaurado.
  const skip = page.getByRole('button', { name: /pular descanso/i });
  await expect(skip.or(page.getByTestId('set-save'))).toBeVisible({ timeout: 30_000 });
  if (await skip.isVisible().catch(() => false)) await skip.click();
  await expect(page.getByTestId('set-save')).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(/SÉRIE 2 DE 4/i)).toBeVisible();

  await logSet(page, 9, 40);
  await expect(page.getByTestId('set-local-chip')).toContainText('salva no aparelho');
  expect(api.serverSets()).toHaveLength(1);

  api.setNetworkDown(false);
  await page.evaluate(() => window.dispatchEvent(new Event('online')));
  await expect.poll(() => api.serverSets().length, { timeout: 30_000 }).toBe(2);
  await expect(page.getByTestId('offline-banner')).toHaveCount(0, { timeout: 20_000 });
  const keys = new Map<string, number>();
  for (const p of api.setPosts.filter((x) => x.status === 200)) keys.set(p.client_key, (keys.get(p.client_key) ?? 0) + 1);
  expect(Array.from(keys.values()).every((n) => n === 1)).toBe(true);
});
