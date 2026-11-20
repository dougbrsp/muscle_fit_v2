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

/** Shell mobile: o "Sair" fica na folha da aba "Mais". */
async function openLogout(page: Page) {
  await page.getByRole('button', { name: 'Mais' }).click();
  await page.getByText('Sair', { exact: true }).click();
}

/** Exercicio cujo grupo ainda nao foi ativado abre no aquecimento opcional; pula para a serie. */
async function skipWarmupIfShown(page: Page) {
  const skip = page.getByRole('button', { name: 'Pular aquecimento' });
  await expect(skip.or(page.getByTestId('set-save'))).toBeVisible();
  if (await skip.isVisible()) await skip.click();
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

test('marcar Aprimoramento de tecnica na apresentacao: o POST de criacao do exercicio leva form_practice true', async ({ page }) => {
  // O Supino ja existe no servidor falso (sem POST de criacao); o Crucifixo (item 7002) ainda nao.
  await seedCrucifixoOnly(page);
  await page.goto('/modo-treino/');
  const start = page.getByRole('button', { name: 'Iniciar exercício' });
  await expect(start).toBeVisible({ timeout: 20_000 });
  await page.getByTestId('briefing-form-practice').click();
  await expect(page.getByTestId('briefing-form-practice-on')).toBeVisible();
  await start.click();
  await skipWarmupIfShown(page);
  await logSet(page, 10, 40);
  await expect(page.getByTestId('rest-clock')).toBeVisible();
  expect(api.exercisePosts).toHaveLength(1);
  expect(api.exercisePosts[0].form_practice).toBe(true);
  expect(api.setPosts.at(0)?.exercise_form_practice).toBe(true);
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

  // Logout real pelo shell mobile (390px): aba "Mais" > "Sair".
  await page.goto('/dashboard/');
  await openLogout(page);
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

async function seedCrucifixoOnly(page: Page) {
  // Fila do treino so com o Crucifixo (item 7002), cujo exercicio ainda NAO existe no servidor.
  await page.addInitScript((session) => {
    window.localStorage.setItem(
      'musclefit.workout-mode-session',
      JSON.stringify({ ...session, cancelled_exercise_ids: [7001, 7003] }),
    );
  }, LEGACY_SESSION);
}

test('exercicio novo offline: addExercise + series na fila, e ao reconectar 1 exercicio e N series sem duplicata', async ({
  page,
  context,
}) => {
  await seedCrucifixoOnly(page);
  await page.goto('/modo-treino/');
  await page.getByTestId('briefing-start').click();
  await skipWarmupIfShown(page);

  api.setNetworkDown(true);
  await context.setOffline(true);
  await logSet(page, 12, 20);
  await expect(page.getByTestId('set-local-chip')).toContainText('salva no aparelho');
  await page.getByRole('button', { name: /pular descanso/i }).click();
  await expect(page.getByText(/SÉRIE 2 DE 3/i)).toBeVisible(); // a tela ja conta as series locais
  await logSet(page, 11, 20);
  await expect(page.getByTestId('set-local-chip')).toContainText('salva no aparelho');
  expect(api.exercisePosts).toHaveLength(0);
  const queued = (await idbKeys(page)).filter((k) => k.startsWith('op_queue/'));
  expect(queued).toHaveLength(3); // 1 addExercise + 2 addSet

  api.setNetworkDown(false);
  await context.setOffline(false);
  await expect.poll(() => api.crucifixoSets().length, { timeout: 40_000 }).toBe(2);
  expect(new Set(api.exercisePosts.map((p) => p.client_key)).size).toBe(1);
  expect(api.exercisePosts.filter((p) => !p.duplicate)).toHaveLength(1);
  const perKey = new Map<string, number>();
  for (const post of api.setPosts.filter((p) => p.status === 200)) perKey.set(post.client_key, (perKey.get(post.client_key) ?? 0) + 1);
  expect(Array.from(perKey.values()).every((n) => n === 1)).toBe(true);
  expect(perKey.size).toBe(2);
  await expect(page.getByTestId('sync-indicator')).toHaveCount(0, { timeout: 20_000 });
});

test('troca de equipamento no meio do exercicio (#225): a continuacao e um exercicio proprio, com serie 1 no exercicio novo', async ({
  page,
}) => {
  await seedCrucifixoOnly(page);
  await page.goto('/modo-treino/');
  await page.getByTestId('briefing-start').click();
  await skipWarmupIfShown(page);

  // Serie 1 no equipamento original: cria o exercicio (chave A).
  await logSet(page, 12, 20);
  await expect.poll(() => api.crucifixoSets().length).toBe(1);
  await page.getByRole('button', { name: /pular descanso/i }).click();

  // Encerra o exercicio trocando de equipamento pelas series restantes.
  await page.getByTestId('execution-finish-exercise').click();
  await page.getByRole('button', { name: /Trocar de equipamento \(séries restantes\)/ }).click();
  await page.getByText('Crucifixo na polia').first().click();
  const start = page.getByTestId('briefing-start');
  await expect(start.or(page.getByTestId('set-save'))).toBeVisible({ timeout: 20_000 });
  if (await start.isVisible()) await start.click();
  await skipWarmupIfShown(page);

  // Serie na continuacao: exercicio novo (chave B), serie numero 1 nele.
  await logSet(page, 10, 15);
  await expect.poll(() => api.exercisePosts.length).toBe(2);
  const keys = api.exercisePosts.map((p) => p.client_key);
  expect(new Set(keys).size).toBe(2);
  expect(api.exercisePosts.every((p) => !p.duplicate)).toBe(true);
  expect(api.exercisePosts.every((p) => p.workout_plan_exercise_id === 7002)).toBe(true);
  const created = api.createdExerciseIds();
  const continuationId = created[created.length - 1];
  expect(continuationId).not.toBe(502);
  await expect.poll(() => api.setsOf(continuationId).map((s) => s.set_number)).toEqual([1]);
  expect(api.crucifixoSets().map((s) => s.set_number)).toEqual([1]);
  expect(api.setPosts.filter((p) => p.status === 409)).toHaveLength(0);
});

test('troca de equipamento (#225) com a continuacao criada e registrada OFFLINE: series 1 e 2 no exercicio novo, sem 409', async ({
  page,
  context,
}) => {
  await seedCrucifixoOnly(page);
  await page.goto('/modo-treino/');
  await page.getByTestId('briefing-start').click();
  await skipWarmupIfShown(page);

  await logSet(page, 12, 20);
  await expect.poll(() => api.crucifixoSets().length).toBe(1);
  await page.getByRole('button', { name: /pular descanso/i }).click();

  // A troca (lista de equipamentos) ainda online; a CRIACAO da continuacao e as series, offline.
  await page.getByTestId('execution-finish-exercise').click();
  await page.getByRole('button', { name: /Trocar de equipamento \(séries restantes\)/ }).click();
  await page.getByText('Crucifixo na polia').first().click();
  const start = page.getByTestId('briefing-start');
  await expect(start.or(page.getByTestId('set-save'))).toBeVisible({ timeout: 20_000 });
  if (await start.isVisible()) await start.click();
  await skipWarmupIfShown(page);

  api.setNetworkDown(true);
  await context.setOffline(true);
  await logSet(page, 10, 15);
  await expect(page.getByTestId('set-local-chip')).toContainText('salva no aparelho');
  await page.getByRole('button', { name: /pular descanso/i }).click();
  await expect(page.getByText(/SÉRIE 2 DE/i)).toBeVisible(); // a continuacao enxerga a serie local dela
  await logSet(page, 9, 15);
  expect(api.exercisePosts).toHaveLength(1); // so o original; a continuacao esta na fila

  api.setNetworkDown(false);
  await context.setOffline(false);
  await expect.poll(() => api.exercisePosts.filter((p) => !p.duplicate).length, { timeout: 40_000 }).toBe(2);
  const keys = api.exercisePosts.map((p) => p.client_key);
  expect(new Set(keys).size).toBe(2);
  const created = api.createdExerciseIds();
  const continuationId = created[created.length - 1];
  expect(continuationId).not.toBe(502);
  await expect.poll(() => api.setsOf(continuationId).map((s) => s.set_number), { timeout: 40_000 }).toEqual([1, 2]);
  expect(api.crucifixoSets().map((s) => s.set_number)).toEqual([1]);
  expect(api.setPosts.filter((p) => p.status === 409)).toHaveLength(0);
});

/** Cenario do seletor (#163): Crucifixo com equipamento atual `Cabo` (carga total); a semente vale so na 1a abertura, para o reload manter o que foi escolhido. */
async function seedEquipmentPicker(page: Page) {
  api.useEquipmentPickerSeed();
  await page.addInitScript((session) => {
    if (window.localStorage.getItem('e2e-seeded-equipment')) return;
    window.localStorage.setItem(
      'musclefit.workout-mode-session',
      JSON.stringify({ ...session, cancelled_exercise_ids: [7001, 7003] }),
    );
    window.localStorage.setItem('e2e-seeded-equipment', '1');
  }, LEGACY_SESSION);
}

/** Antes da 1a serie: toca no equipamento, busca "poli" e escolhe a Polia (carga por lado, diferente da atual), confirmando o aviso. */
async function pickPoliaBeforeFirstSet(page: Page) {
  await page.goto('/modo-treino/');
  await expect(page.getByTestId('briefing-start')).toBeVisible({ timeout: 20_000 });
  await page.getByTestId('title-equipment-swap').first().click();

  const search = page.getByLabel('Buscar equipamento');
  await expect(page.getByText('Voador')).toBeVisible();
  await search.fill('poli');
  await expect(page.getByText('Voador')).toHaveCount(0);
  await expect(page.getByText('Crucifixo na polia').first()).toBeVisible();

  await page.getByText('Crucifixo na polia').first().click();
  await expect(page.getByTestId('equipment-convention-warning')).toContainText('Neste equipamento a carga é registrada:');
  await page.getByRole('button', { name: 'Confirmar' }).click();
  await expect(page.getByTestId('title-equipment-swap').first()).toContainText('Polia');
}

test('seletor de equipamento (#163): buscar, aviso de convencao, confirmar, 1a serie com o equipment_id escolhido e escolha mantida ao recarregar', async ({
  page,
}) => {
  await seedEquipmentPicker(page);
  await pickPoliaBeforeFirstSet(page);

  // Recarregar antes da 1a serie mantem a escolha (guardada no aparelho).
  await page.reload();
  await expect(page.getByTestId('title-equipment-swap').first()).toContainText('Polia', { timeout: 20_000 });

  const start = page.getByTestId('briefing-start');
  await expect(start.or(page.getByTestId('set-save'))).toBeVisible({ timeout: 20_000 });
  if (await start.isVisible()) await start.click();
  await skipWarmupIfShown(page);
  await logSet(page, 12, 20);
  await expect.poll(() => api.exercisePosts.length).toBe(1);
  expect(api.exercisePosts[0].equipment_id).toBe(901);
});

test('sem historico no equipamento escolhido: o card "Treino anterior" mostra "Sem historico neste equipamento" (#163)', async ({
  page,
}) => {
  await seedEquipmentPicker(page);
  api.useEmptyLastSessionSets();
  await pickPoliaBeforeFirstSet(page);

  const start = page.getByTestId('briefing-start');
  await expect(start.or(page.getByTestId('set-save'))).toBeVisible({ timeout: 20_000 });
  if (await start.isVisible()) await start.click();
  await skipWarmupIfShown(page);
  await expect(page.getByTestId('previous-session-empty')).toContainText('Sem histórico neste equipamento');
  expect(api.lastSessionQueries).toContain('901');
});

test('depois de aberto, 503 na revalidacao (/auth/me) ao voltar a rede NAO derruba o treino', async ({ page }) => {
  await openWorkout(page);
  api.setAuthMeStatus(503);
  await page.evaluate(() => window.dispatchEvent(new Event('online')));
  await page.waitForTimeout(6000); // retries do GET + revalidacao
  await expect(page.getByTestId('set-save')).toBeVisible();
  await expect(page.getByText('Não foi possível abrir o treino')).toHaveCount(0);
  await logSet(page, 10, 40);
  await expect.poll(() => api.serverSets().length).toBe(1);
});

test('reabrir com /auth/me em 503 usa o usuario conhecido (recente) e mostra o aviso offline', async ({ page }) => {
  await openWorkout(page);
  api.setAuthMeStatus(503);
  await page.reload();
  await expect(page.getByTestId('offline-banner')).toContainText('Offline', { timeout: 30_000 });
  await expect(page.getByTestId('set-save').or(page.getByRole('button', { name: /iniciar exercício/i }))).toBeVisible({
    timeout: 30_000,
  });
});

test('401 em /auth/me nunca usa o fallback offline', async ({ page }) => {
  await openWorkout(page);
  api.setAuthMeStatus(401);
  await page.reload();
  await expect(page.getByTestId('offline-banner')).toHaveCount(0);
  await expect(page.getByTestId('set-save')).toHaveCount(0);
});

test('duas abas: a segunda avisa "treino aberto em outra aba" e assume ao fechar a primeira', async ({ page, context }) => {
  await openWorkout(page);
  const second = await context.newPage();
  const secondApi = await installWorkoutApi(second);
  void secondApi;
  await second.goto('/modo-treino/');
  await expect(second.getByTestId('tab-locked-notice')).toContainText('outra aba', { timeout: 20_000 });
  await expect(second.getByTestId('set-save')).toHaveCount(0);

  await page.close();
  await expect(second.getByTestId('tab-locked-notice')).toHaveCount(0, { timeout: 30_000 });
  await expect(second.getByTestId('set-save').or(second.getByRole('button', { name: /iniciar exercício/i }))).toBeVisible({
    timeout: 30_000,
  });
});

test('Modo Intensivo offline: fica "salvando" ate o 2xx; recusa do servidor vira erro e descartar nunca vira "salva"', async ({
  page,
  context,
}) => {
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
  // 1a serie ja cria o exercicio; o corte de rede vem so depois dele existir.
  await page.getByTestId('intensive-complete-set').click();
  await expect.poll(() => api.fstBodies.length).toBe(1);
  await page.getByTestId('rest-primary-cta').click();

  api.setNetworkDown(true);
  await context.setOffline(true);
  await page.getByTestId('intensive-reps-input').fill('9');
  await page.getByTestId('intensive-complete-set').click();
  await expect(page.getByTestId('sync-indicator')).toContainText('aguardando envio');
  expect(api.fstBodies).toHaveLength(1);

  api.setNetworkDown(false);
  api.failNextSetPosts(1000, 422);
  await context.setOffline(false);
  await expect(page.getByTestId('sync-indicator')).toContainText('Não foi possível enviar', { timeout: 40_000 });
  expect(api.fstBodies).toHaveLength(1); // nada foi gravado

  // Descartar (manter o do servidor) nao e sucesso: a serie segue sem estar salva.
  await page.getByRole('button', { name: 'Manter o do servidor' }).click();
  await expect(page.getByTestId('sync-indicator')).toHaveCount(0);
  expect(api.fstBodies).toHaveLength(1); // descartada: nunca virou serie salva no servidor
});

test('logout com serie pendente pede confirmacao; cancelar mantem a fila, confirmar apaga', async ({ page }) => {
  await openWorkout(page);
  api.failNextSetPosts(1000, 503);
  await logSet(page, 10, 40);
  await expect(page.getByTestId('sync-indicator')).toContainText('aguardando envio');

  const messages: string[] = [];
  let accept = false;
  page.on('dialog', async (dialog) => {
    messages.push(dialog.message());
    if (accept) await dialog.accept();
    else await dialog.dismiss();
  });

  await page.goto('/dashboard/');
  await openLogout(page);
  await expect.poll(() => messages.length).toBe(1);
  expect(messages[0]).toMatch(/1 série não sincronizada/);
  await expect(page).toHaveURL(/dashboard/); // cancelou: continua logado
  expect((await idbKeys(page)).filter((k) => k.startsWith('op_queue/v1:1:op:'))).toHaveLength(1);

  accept = true;
  await openLogout(page);
  await expect(page).toHaveURL(/\/$/);
  await expect.poll(async () => (await idbKeys(page)).filter((k) => k.includes('v1:1:')).length).toBe(0);
});

test('editar serie de exercicio criado offline: depois do sync o PATCH vai ao id REAL, nunca ao id local', async ({ page, context }) => {
  await page.addInitScript((session) => {
    window.localStorage.setItem(
      'musclefit.workout-mode-session',
      JSON.stringify({ ...session, advanced_mode: true, cancelled_exercise_ids: [7001, 7002] }),
    );
  }, LEGACY_SESSION);
  await page.goto('/modo-treino/');
  await page.getByTestId('briefing-start').click();
  await page.getByTestId('intensive-start').click();

  api.setNetworkDown(true);
  await context.setOffline(true);
  await page.getByTestId('intensive-complete-set').click(); // sem reps: fica pendente para confirmar
  await expect(page.getByTestId('sync-indicator')).toContainText('aguardando envio');
  expect(api.exercisePosts).toHaveLength(0);

  api.setNetworkDown(false);
  await context.setOffline(false);
  await expect.poll(() => api.fstBodies.length, { timeout: 40_000 }).toBe(1);
  expect(api.exercisePosts.filter((p) => !p.duplicate)).toHaveLength(1);

  // Confirma as reps pendentes pelo chip: PATCH ao exercicio real (503), nunca a um id negativo.
  const chip = page.getByTestId('intensive-pending-chip');
  await expect(chip).toBeVisible({ timeout: 20_000 });
  await chip.getByRole('spinbutton').fill('9');
  await chip.getByRole('button', { name: 'OK' }).click();
  await expect.poll(() => api.patches.length, { timeout: 20_000 }).toBe(1);
  expect(api.patches[0]).toMatchObject({ exerciseId: '503', body: { reps: 9 } });
});

test('duas abas: "Tentar de novo" na segunda e fechar a primeira -> a segunda assume sozinha, uma vez so', async ({ page, context }) => {
  await openWorkout(page);
  const second = await context.newPage();
  await installWorkoutApi(second);
  await second.goto('/modo-treino/');
  await expect(second.getByTestId('tab-locked-notice')).toBeVisible({ timeout: 20_000 });
  await second.getByRole('button', { name: 'Tentar de novo' }).click();
  await expect(second.getByTestId('tab-locked-notice')).toBeVisible();

  await page.close();
  await expect(second.getByTestId('tab-locked-notice')).toHaveCount(0, { timeout: 30_000 });
  await expect(second.getByTestId('set-save').or(second.getByRole('button', { name: /iniciar exercício/i }))).toBeVisible({
    timeout: 30_000,
  });
});
