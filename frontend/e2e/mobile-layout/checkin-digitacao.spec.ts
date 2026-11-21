import { expect, test, type Page, type Route } from '@playwright/test';
import { FROZEN_NOW, installMockApi, MOCK_API_ORIGIN } from '../desktop-baseline/mock-api';
import { resolveFixture } from './evolucao-fixtures';

/**
 * Issue #250: digitar um comentario no check-in diario derrubava o app inteiro no iPhone
 * ("Algo deu errado. Um erro inesperado impediu o MuscleFit de carregar."). Roda nos projetos
 * `webkit-iphone` (iPhone 13, WebKit) e `chromium-390` (ver playwright.mobile.config.ts):
 *
 *   npx playwright test -c e2e/mobile-layout/playwright.mobile.config.ts checkin-digitacao --project=webkit-iphone --project=chromium-390
 */
const FALLBACK_GLOBAL = /Algo deu errado/;
const CAMPO = '#mob-checkin-nota';
const DELAY = 15;

async function prepare(page: Page) {
  await page.clock.setFixedTime(FROZEN_NOW);
  const { unhandled } = await installMockApi(page);
  let getCount = 0;
  await page.route(`${MOCK_API_ORIGIN}/**`, async (route) => {
    const req = route.request();
    if (req.method() !== 'GET') return route.fallback();
    const path = new URL(req.url()).pathname;
    if (path === '/daily-checkins') getCount += 1;
    const payload = resolveFixture(`GET ${path}`);
    if (payload === undefined) return route.fallback();
    return fulfillJson(route, payload);
  });
  await page.addInitScript(() => window.localStorage.setItem('musclefit.flag.mobile-ui', 'on'));
  const erros: string[] = [];
  page.on('pageerror', (e) => erros.push(`pageerror: ${e.message}\n${e.stack ?? ''}`));
  page.on('console', (m) => {
    if (m.type() === 'error') erros.push(`console.error: ${m.text()}`);
  });
  return { unhandled, erros, getCount: () => getCount };
}

function fulfillJson(route: Route, payload: unknown, status = 200) {
  const req = route.request();
  return route.fulfill({
    status,
    contentType: 'application/json',
    headers: { 'access-control-allow-origin': req.headers()['origin'] ?? '*', 'access-control-allow-credentials': 'true' },
    body: JSON.stringify(payload),
  });
}

async function abrirCheckin(page: Page) {
  await page.goto('/dashboard/checkin/');
  await page.locator('[data-mobile-shell="true"]').waitFor({ timeout: 20_000 });
  await expect(page.getByText('Disposição').first()).toBeVisible({ timeout: 20_000 });
}

/** Afirma que nada caiu e que o campo mantem o texto; a mensagem inclui os erros capturados. */
async function intacto(page: Page, erros: string[], esperado: string, etapa: string) {
  const ctx = `${etapa}\nerros capturados:\n${erros.join('\n---\n') || '(nenhum)'}`;
  await expect(page.getByText(FALLBACK_GLOBAL), `fallback global apareceu em: ${ctx}`).toHaveCount(0);
  await expect(page.locator(CAMPO), `campo perdeu o texto em: ${ctx}`).toHaveValue(esperado);
}

test.describe('check-in diario: digitar o comentario (#250)', () => {
  test('digitacao crescente (curto, acentos, emoji, ate perto do limite), apagar e colar nao derrubam a tela', async ({ page }) => {
    const { erros, unhandled } = await prepare(page);
    await abrirCheckin(page);
    const campo = page.locator(CAMPO);
    await campo.focus();

    let texto = '';
    const etapas: Array<[string, string]> = [
      ['curto', 'Dormi bem'],
      ['acentos', ' e acordei com disposição, ânimo e coração leve'],
      ['emoji', ' 💪🏽😴🔥'],
      ['perto do limite', ' x'.repeat(90)],
    ];
    for (const [nome, trecho] of etapas) {
      await campo.pressSequentially(trecho, { delay: DELAY });
      texto += trecho;
      await intacto(page, erros, texto, `digitacao: ${nome}`);
    }
    expect(texto.length, 'a ultima etapa fica abaixo do limite').toBeLessThanOrEqual(255);

    await campo.press('Backspace');
    texto = texto.slice(0, -1);
    await intacto(page, erros, texto, 'apagar 1 caractere');
    await campo.fill('');
    await intacto(page, erros, '', 'apagar tudo');

    const longo = 'Treino pesado, ombro incomodando na descida. '.repeat(5).slice(0, 250);
    await campo.fill(longo);
    await intacto(page, erros, longo, 'colar texto longo (fill)');

    // Colagem em navegador que ignore maxLength: a validacao mostra o erro junto do campo, sem cair.
    await campo.fill('y'.repeat(300));
    if ((await campo.inputValue()).length > 255) {
      await page.getByRole('button', { name: /Salvar correção|Registrar/ }).click();
      await expect(page.getByText('Máximo de 255 caracteres.')).toBeVisible();
    }
    await expect(page.getByText(FALLBACK_GLOBAL)).toHaveCount(0);

    expect(erros, `erros de pagina/console durante a digitacao:\n${erros.join('\n---\n')}`).toEqual([]);
    expect(unhandled, 'chamadas nao mockadas').toEqual([]);
  });

  test('refetch dos dados do dia durante a digitacao nao apaga o texto', async ({ page }) => {
    const { erros, getCount } = await prepare(page);
    // Mesmo registro do dia (id 1), mas objeto novo a cada GET (updated_at muda): o que um refetch real devolve.
    let versao = 0;
    await page.route(`${MOCK_API_ORIGIN}/daily-checkins`, async (route) => {
      if (route.request().method() !== 'GET') return route.fallback();
      const base = JSON.parse(JSON.stringify(resolveFixture('GET /daily-checkins'))) as { items: Array<Record<string, unknown>> };
      versao += 1;
      base.items[0].updated_at = `2026-10-14T15:0${versao}:00Z`;
      return fulfillJson(route, base);
    });
    await abrirCheckin(page);
    const campo = page.locator(CAMPO);
    await campo.focus();
    await campo.pressSequentially('Comecei a escrever', { delay: DELAY });

    const antes = getCount();
    // refetchOnWindowFocus esta desligado (providers.tsx); o caminho real e a rede voltar (refetchOnReconnect)
    // com os dados velhos: avanca o relogio alem do staleTime (60 s), cai e volta a conexao.
    await page.clock.setFixedTime(new Date(FROZEN_NOW.getTime() + 5 * 60_000));
    await page.context().setOffline(true);
    await page.context().setOffline(false);
    await expect.poll(getCount, { message: 'refetch de /daily-checkins' }).toBeGreaterThan(antes);

    await campo.pressSequentially(' e continuei', { delay: DELAY });
    await intacto(page, erros, 'Comecei a escrever e continuei', 'depois do refetch');
    expect(erros, `erros capturados:\n${erros.join('\n---\n')}`).toEqual([]);
  });

  test('envio com 422 e depois 500: mostra a mensagem, mantem o texto e nao cai no fallback', async ({ page }) => {
    const { erros } = await prepare(page);
    const respostas = [
      { status: 422, body: { detail: 'Nota inválida (e2e)' } },
      { status: 500, body: { detail: 'Falha interna (e2e)' } },
    ];
    let envios = 0;
    await page.route(`${MOCK_API_ORIGIN}/daily-checkins`, async (route) => {
      if (route.request().method() !== 'POST') return route.fallback();
      const r = respostas[Math.min(envios, respostas.length - 1)];
      envios += 1;
      return fulfillJson(route, r.body, r.status);
    });
    await abrirCheckin(page);
    const texto = 'Nota que não pode se perder 💪';
    await page.locator(CAMPO).fill(texto);
    const salvar = page.getByRole('button', { name: /Salvar correção|Registrar/ });

    // O teclado aberto esconde a navegacao e o botao desce; o usuario fecha o teclado antes de tocar.
    await page.locator(CAMPO).blur();
    await expect(page.locator('[data-mobile-shell="true"] [data-keyboard="open"], [data-mobile-shell="true"][data-keyboard="open"]')).toHaveCount(0);
    await expect(page.locator('[data-mobile-shell="true"] nav')).toBeVisible();
    await salvar.click();
    await expect.poll(() => envios).toBe(1);
    await expect(page.getByText(/Nota inválida \(e2e\)/)).toBeVisible();
    await intacto(page, erros.filter((e) => !/422/.test(e)), texto, 'depois do 422');

    await salvar.click();
    await expect.poll(() => envios).toBe(2);
    await expect(page.locator('.mantine-Notification-root').last()).toBeVisible();
    await intacto(page, erros.filter((e) => !/422|500/.test(e)), texto, 'depois do 500');
    await expect(page.locator('[data-mobile-shell="true"] nav')).toBeVisible();
    expect(
      erros.filter((e) => e.startsWith('pageerror')),
      `pageerror durante o envio com falha:\n${erros.join('\n---\n')}`,
    ).toEqual([]);
  });
});
