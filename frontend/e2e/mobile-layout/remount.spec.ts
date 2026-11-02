import { expect, test } from '@playwright/test';
import { FROZEN_NOW, installMockApi } from '../desktop-baseline/mock-api';

// MB-10: cruzar 768 px remonta a pagina. Prova o comportamento escolhido e documentado:
// a URL e preservada; o estado local (campo digitado) e perdido.
test('cruzar 768px: URL preservada, campo digitado perdido (documentado, MB-10)', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 800 });
  await page.clock.setFixedTime(FROZEN_NOW);
  await installMockApi(page);
  await page.addInitScript(() => window.localStorage.setItem('musclefit.flag.mobile-ui', 'on'));
  await page.goto('/perfil/');
  const nome = page.getByLabel('Nome completo');
  await expect(nome).toBeVisible({ timeout: 20_000 });
  await nome.fill('Texto digitado');
  await page.setViewportSize({ width: 1024, height: 768 });
  await expect(page.getByRole('navigation', { name: 'Menu principal' })).toBeVisible();
  await page.setViewportSize({ width: 375, height: 800 });
  await expect(page.locator('[data-mobile-shell="true"]')).toBeVisible();
  expect(new URL(page.url()).pathname).toBe('/perfil/');
  await expect(page.getByLabel('Nome completo')).not.toHaveValue('Texto digitado');
});
