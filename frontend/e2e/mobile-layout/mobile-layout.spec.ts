import { expect, test, type Page } from '@playwright/test';
import { FROZEN_NOW, installMockApi } from '../desktop-baseline/mock-api';

/**
 * Layout mobile (Frente A, A1): seletor atras da flag, shell com bottom nav,
 * sem overflow horizontal, navegacao preservando URL. Larguras do plano.
 */
const PORTRAIT = [320, 375, 390, 414, 430] as const;
// Paisagem de celular: 667 ainda e mobile (< 768). 844 ja e >= 768 e cai no
// desktop por decisao do plano (3.1: sem estado hibrido, corte = `sm` do Mantine).
const LANDSCAPE = [{ width: 667, height: 375 }] as const;
const TABLET = [
  { width: 844, height: 390 },
  { width: 768, height: 1024 },
  { width: 1024, height: 768 },
] as const;
const FLAG_KEY = 'musclefit.flag.mobile-ui';
const SHELL = '[data-mobile-shell="true"]';
const READY = /voce concluiu 10 de 12|pronto para treinar/i;

async function open(
  page: Page,
  path: string,
  flag: 'on' | 'off' | null,
  size: { width: number; height: number },
) {
  await page.setViewportSize(size);
  await page.clock.setFixedTime(FROZEN_NOW);
  await installMockApi(page);
  if (flag) {
    await page.addInitScript(([k, v]) => window.localStorage.setItem(k, v), [FLAG_KEY, flag]);
  }
  await page.goto(path);
}

async function noHorizontalOverflow(page: Page) {
  const { scrollWidth, innerWidth } = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
  }));
  expect(scrollWidth, 'scrollWidth > largura da viewport').toBeLessThanOrEqual(innerWidth);
}

const sizes = [
  ...PORTRAIT.map((w) => ({ name: `${w}-retrato`, width: w, height: 800 })),
  ...LANDSCAPE.map((s) => ({ name: `${s.width}-paisagem`, ...s })),
];

for (const size of sizes) {
  test(`mobile ${size.name}: shell na mesma URL, sem overflow, alvos >= 44px`, async ({ page }) => {
    await open(page, '/dashboard/', 'on', size);
    await expect(page.locator(SHELL)).toBeVisible({ timeout: 20_000 });
    expect(new URL(page.url()).pathname).toBe('/dashboard/');
    await expect(page.getByText(READY).first()).toBeVisible({ timeout: 20_000 });
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: `e2e/mobile-layout/__screens__/dashboard-${size.name}.png` });

    await expect(page.getByRole('navigation', { name: 'Menu principal' })).toHaveCount(0);

    const nav = page.getByRole('navigation', { name: 'Navegação principal' });
    const tabs = nav.locator('a, button');
    await expect(tabs).toHaveCount(6);
    for (let i = 0; i < 6; i += 1) {
      const box = await tabs.nth(i).boundingBox();
      expect(box!.height, `aba ${i} altura`).toBeGreaterThanOrEqual(44);
      expect(box!.width, `aba ${i} largura`).toBeGreaterThanOrEqual(44);
    }
    const navBox = await nav.boundingBox();
    expect(navBox!.x).toBeGreaterThanOrEqual(0);
    expect(navBox!.x + navBox!.width).toBeLessThanOrEqual(size.width);
    await noHorizontalOverflow(page);
  });
}

for (const size of TABLET) {
  test(`tablet ${size.width}x${size.height}: cai no desktop mesmo com a flag ligada`, async ({ page }) => {
    await open(page, '/dashboard/', 'on', size);
    await expect(page.getByRole('navigation', { name: 'Menu principal' })).toBeVisible({ timeout: 20_000 });
    await expect(page.locator(SHELL)).toHaveCount(0);
  });
}

test('flag desligada em 375: arvore desktop, sem shell', async ({ page }) => {
  await open(page, '/dashboard/', 'off', { width: 375, height: 800 });
  await expect(page.getByText(READY).first()).toBeVisible({ timeout: 20_000 });
  await expect(page.locator(SHELL)).toHaveCount(0);
});

test('?mobile=1 liga a flag so para QA', async ({ page }) => {
  await open(page, '/dashboard/?mobile=1', null, { width: 375, height: 800 });
  await expect(page.locator(SHELL)).toBeVisible({ timeout: 20_000 });
});

test('bottom nav navega; voltar, avancar e refresh preservam a rota', async ({ page }) => {
  await open(page, '/dashboard/', 'on', { width: 390, height: 800 });
  await expect(page.locator(SHELL)).toBeVisible({ timeout: 20_000 });
  const nav = page.getByRole('navigation', { name: 'Navegação principal' });

  await nav.getByRole('link', { name: 'Histórico' }).click();
  await expect(page).toHaveURL(/\/registro-exercicios\/?$/);
  await expect(page.locator(SHELL)).toBeVisible();
  await expect(nav.getByRole('link', { name: 'Histórico' })).toHaveAttribute('aria-current', 'page');

  await nav.getByRole('link', { name: 'Biblioteca' }).click();
  await expect(page).toHaveURL(/\/exercicios\/?$/);

  await page.goBack();
  await expect(page).toHaveURL(/\/registro-exercicios\/?$/);
  await expect(page.locator(SHELL)).toBeVisible();
  await page.goForward();
  await expect(page).toHaveURL(/\/exercicios\/?$/);

  await page.reload();
  await expect(page).toHaveURL(/\/exercicios\/?$/);
  await expect(page.locator(SHELL)).toBeVisible({ timeout: 20_000 });
  await expect(nav.getByRole('link', { name: 'Biblioteca' })).toHaveAttribute('aria-current', 'page');
});

test('"Mais" lista todas as demais rotas e navega', async ({ page }) => {
  await open(page, '/dashboard/', 'on', { width: 375, height: 800 });
  await expect(page.locator(SHELL)).toBeVisible({ timeout: 20_000 });
  await page.getByRole('button', { name: 'Mais' }).click();
  const sheet = page.getByRole('dialog', { name: 'Menu' });
  await expect(sheet).toBeVisible();
  for (const label of [
    'Progressão',
    'Recuperação',
    'Relatórios',
    'Nutrição',
    'Insights',
    'Planos de treino',
    'Academias',
    'Sair',
  ]) {
    await expect(sheet.getByText(label, { exact: true }).first(), label).toBeVisible();
  }
  await page.screenshot({ path: 'e2e/mobile-layout/__screens__/mais-375.png' });
  await sheet.getByRole('link', { name: 'Nutrição' }).click();
  await expect(page).toHaveURL(/\/nutricao\/?$/);
  await expect(sheet).toBeHidden();
  await expect(page.getByRole('button', { name: 'Mais' })).toHaveAttribute('aria-current', 'page');
});

test('rotacao e troca de viewport mudam so a apresentacao', async ({ page }) => {
  await open(page, '/dashboard/', 'on', { width: 390, height: 800 });
  await expect(page.locator(SHELL)).toBeVisible({ timeout: 20_000 });
  await page.setViewportSize({ width: 667, height: 375 });
  await expect(page.locator(SHELL)).toBeVisible();
  await page.setViewportSize({ width: 1024, height: 768 });
  await expect(page.getByRole('navigation', { name: 'Menu principal' })).toBeVisible();
  await expect(page.locator(SHELL)).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 800 });
  await expect(page.locator(SHELL)).toBeVisible();
  expect(new URL(page.url()).pathname).toBe('/dashboard/');
});

test('A2: campos e botoes das paginas P1 tem >= 44px e fonte >= 16px no shell', async ({ page }) => {
  await open(page, '/perfil/', 'on', { width: 375, height: 800 });
  await expect(page.locator(SHELL)).toBeVisible({ timeout: 20_000 });
  const nome = page.getByLabel('Nome completo');
  await expect(nome).toBeVisible({ timeout: 20_000 });
  const box = await nome.boundingBox();
  expect(box!.height).toBeGreaterThanOrEqual(44);
  const fontSize = await nome.evaluate((el) => parseFloat(getComputedStyle(el).fontSize));
  expect(fontSize).toBeGreaterThanOrEqual(16);
  await noHorizontalOverflow(page);
  await page.screenshot({ path: 'e2e/mobile-layout/__screens__/perfil-375.png' });
});

test('A2: historico, biblioteca e treinos sem overflow de pagina em 320', async ({ page }) => {
  await open(page, '/dashboard/treinos/', 'on', { width: 320, height: 800 });
  await expect(page.locator(SHELL)).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText(/costas e biceps/i).first()).toBeVisible({ timeout: 20_000 });
  await noHorizontalOverflow(page);
  for (const path of ['/registro-exercicios/', '/exercicios/']) {
    await page.goto(path);
    await expect(page.locator(SHELL)).toBeVisible({ timeout: 20_000 });
    await page.waitForLoadState('networkidle');
    await noHorizontalOverflow(page);
  }
});
