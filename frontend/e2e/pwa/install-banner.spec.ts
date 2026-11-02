import { expect, test } from '@playwright/test';

import { installMockApi } from '../desktop-baseline/mock-api';

const IOS_SAFARI =
  'Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1';
const IOS_CHROME =
  'Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/124.0 Mobile/15E148 Safari/604.1';

const banner = (page: import('@playwright/test').Page) => page.getByTestId('pwa-install-banner');

test.describe('convite de instalação (iOS Safari)', () => {
  test.use({ userAgent: IOS_SAFARI, viewport: { width: 390, height: 844 }, serviceWorkers: 'block' });

  test('flag ligada, viewport mobile: mostra a instrução manual e dispensa persistindo', async ({ page }) => {
    await installMockApi(page, { anonymous: true });
    await page.goto('/?mobile=1');
    await expect(banner(page)).toBeVisible();
    await expect(page.getByTestId('pwa-install-ios-text')).toHaveText(/Safari → Compartilhar → Adicionar à Tela de Início/);

    // alvo de toque do botão de dispensar >= 44 px
    const close = page.getByRole('button', { name: /dispensar convite/i });
    const box = await close.boundingBox();
    expect(box!.width).toBeGreaterThanOrEqual(44);
    expect(box!.height).toBeGreaterThanOrEqual(44);

    await close.click();
    await expect(banner(page)).toHaveCount(0);
    await page.reload();
    await page.waitForLoadState('networkidle');
    await expect(banner(page)).toHaveCount(0);
  });

  test('dispensado há mais de 30 dias volta a aparecer', async ({ page }) => {
    await installMockApi(page, { anonymous: true });
    await page.addInitScript(() => {
      localStorage.setItem('musclefit.pwa.install-dismissed-at', String(Date.now() - 31 * 24 * 60 * 60 * 1000));
    });
    await page.goto('/?mobile=1');
    await expect(banner(page)).toBeVisible();
  });

  test('flag desligada: nunca aparece', async ({ page }) => {
    await installMockApi(page, { anonymous: true });
    await page.goto('/?mobile=0');
    await page.waitForLoadState('networkidle');
    await expect(banner(page)).toHaveCount(0);
  });

  test('já instalado (standalone): não aparece', async ({ page }) => {
    await installMockApi(page, { anonymous: true });
    await page.addInitScript(() => {
      Object.defineProperty(navigator, 'standalone', { value: true, configurable: true });
    });
    await page.goto('/?mobile=1');
    await page.waitForLoadState('networkidle');
    await expect(banner(page)).toHaveCount(0);
  });
});

test.describe('convite de instalação (fora do escopo)', () => {
  test.use({ serviceWorkers: 'block' });

  test('Chrome no iOS não recebe a instrução do Safari', async ({ browser }) => {
    const ctx = await browser.newContext({ userAgent: IOS_CHROME, viewport: { width: 390, height: 844 } });
    const page = await ctx.newPage();
    await installMockApi(page, { anonymous: true });
    await page.goto('/?mobile=1');
    await page.waitForLoadState('networkidle');
    await expect(banner(page)).toHaveCount(0);
    await ctx.close();
  });

  test('desktop (largura >= 768) com flag ligada: não aparece', async ({ browser }) => {
    const ctx = await browser.newContext({ userAgent: IOS_SAFARI, viewport: { width: 1024, height: 768 } });
    const page = await ctx.newPage();
    await installMockApi(page, { anonymous: true });
    await page.goto('/?mobile=1');
    await page.waitForLoadState('networkidle');
    await expect(banner(page)).toHaveCount(0);
    await ctx.close();
  });

  test('Android: beforeinstallprompt mostra o botão Instalar', async ({ browser }) => {
    const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
    const page = await ctx.newPage();
    await installMockApi(page, { anonymous: true });
    await page.goto('/?mobile=1');
    await page.waitForLoadState('networkidle');
    await expect(banner(page)).toHaveCount(0); // sem evento, sem convite
    await page.evaluate(() => window.dispatchEvent(new Event('beforeinstallprompt')));
    await expect(banner(page)).toBeVisible();
    await expect(page.getByRole('button', { name: 'Instalar' })).toBeVisible();
    await ctx.close();
  });
});
