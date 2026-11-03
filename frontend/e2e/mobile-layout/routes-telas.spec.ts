import { mkdirSync } from 'node:fs';
import { expect, test } from '@playwright/test';
import { FROZEN_NOW, installMockApi } from '../desktop-baseline/mock-api';

/**
 * Mobile v2, frente C: as telas migradas (treino, corpo, nutricao e conta)
 * montam no shell mobile em 390x844, sem scroll horizontal da pagina e sem
 * `<table>` do Mantine. Grava uma captura por rota em
 * `MOBILE_V2_C_SHOTS` (padrao: .omc/artifacts/mobile-v2/c).
 */
const ROUTES = [
  '/registro-exercicios/',
  '/registro-exercicios/1/',
  '/exercicios/',
  '/planos-treino/',
  '/planos-treino/1/',
  '/nutricao/',
  '/nutricao/favoritos/',
  '/usuarios/',
];

const SHOTS = process.env.MOBILE_V2_C_SHOTS ?? 'C:/muscle_fit/.omc/artifacts/mobile-v2/c';

test.describe('telas da frente C em 390x844', () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test('rotas migradas: sem overflow de pagina e sem <table>', async ({ page }) => {
    test.setTimeout(300_000);
    mkdirSync(SHOTS, { recursive: true });
    await page.clock.setFixedTime(FROZEN_NOW);
    await installMockApi(page);
    await page.addInitScript(() => window.localStorage.setItem('musclefit.flag.mobile-ui', 'on'));
    for (const route of ROUTES) {
      await page.goto(route);
      await page.locator('[data-mobile-shell="true"]').waitFor({ timeout: 20_000 });
      await page.waitForLoadState('networkidle').catch(() => undefined);
      const m = await page.evaluate(() => ({
        sw: document.documentElement.scrollWidth,
        iw: window.innerWidth,
        tables: document.querySelectorAll('main table, main .mantine-Table-table').length,
        h1: document.querySelectorAll('main h1').length,
      }));
      expect(m.sw, `overflow de pagina em ${route}`).toBeLessThanOrEqual(m.iw);
      expect(m.tables, `<table> em ${route}`).toBe(0);
      await page.screenshot({ path: `${SHOTS}/${route.replace(/\//g, '_')}.png` });
    }
  });
});
