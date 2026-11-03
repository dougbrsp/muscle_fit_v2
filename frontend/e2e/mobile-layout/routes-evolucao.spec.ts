import { expect, test } from '@playwright/test';
import { FROZEN_NOW, installMockApi, MOCK_API_ORIGIN } from '../desktop-baseline/mock-api';
import { resolveFixture } from './evolucao-fixtures';

/**
 * Mobile v2, frente B (Evolucao e dashboards): as 18 rotas migradas montam a
 * TELA MOBILE (titulo grande do ScreenHeader), nao rolam na horizontal da
 * pagina e nao renderizam nenhum <table> (criterio 4), em 320/390/430 px e
 * em paisagem. Grava a captura 390x844 (viewport inicial) de cada rota em
 * .omc/artifacts/mobile-v2/b/ (fora do git).
 */
const ROUTES = [
  '/dashboard/treinos/',
  '/dashboard/progressao/',
  '/dashboard/recovery/',
  '/dashboard/musculos/',
  '/dashboard/corpo/',
  '/dashboard/cardio/',
  '/dashboard/nutricao/',
  '/dashboard/ciclo/',
  '/dashboard/contexto/',
  '/dashboard/checkin/',
  '/dashboard/engine/',
  '/dashboard/data-quality/',
  '/dashboard/relatorios/',
  '/dashboard/insights/',
  '/dashboard/exercicios/1/',
  '/insights/',
  '/agente-cardio/',
  '/intervals-icu/',
];

const VIEWPORTS = [
  { name: '320', width: 320, height: 800, touch: false },
  { name: '390', width: 390, height: 844, touch: false },
  { name: '430', width: 430, height: 932, touch: false },
  { name: '844x390-paisagem', width: 844, height: 390, touch: true },
] as const;

const FULL_PAGE = new Set(['dashboard/musculos', 'dashboard/cardio', 'dashboard/treinos', 'dashboard/progressao', 'intervals-icu']);

const SHOTS_DIR = 'C:/muscle_fit/.omc/artifacts/mobile-v2/b';

for (const vp of VIEWPORTS) {
  test.describe(`evolucao ${vp.name}`, () => {
    test.use({ hasTouch: vp.touch, isMobile: vp.touch });

    test(`rotas da Evolucao em ${vp.name}`, async ({ page }) => {
      test.setTimeout(600_000);
      const out: Record<string, unknown>[] = [];
      await page.setViewportSize({ width: vp.width, height: vp.height });
      await page.clock.setFixedTime(FROZEN_NOW);
      await installMockApi(page);
      // Fixtures realistas SO desta spec (por cima do mock-api, que nao muda); o que nao estiver aqui cai no mock-api.
      await page.route(`${MOCK_API_ORIGIN}/**`, async (route) => {
        const req = route.request();
        if (req.method() !== 'GET') return route.fallback();
        const payload = resolveFixture(`GET ${new URL(req.url()).pathname}`);
        if (payload === undefined) return route.fallback();
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          headers: {
            'access-control-allow-origin': req.headers()['origin'] ?? '*',
            'access-control-allow-credentials': 'true',
          },
          body: JSON.stringify(payload),
        });
      });
      const pageErrors: string[] = [];
      page.on('pageerror', (e) => pageErrors.push(e.message.slice(0, 200)));
      await page.addInitScript(() => window.localStorage.setItem('musclefit.flag.mobile-ui', 'on'));
      for (const route of ROUTES) {
        await page.goto(route);
        await page.locator('[data-mobile-shell="true"]').waitFor({ timeout: 20_000 }).catch(() => undefined);
        await page.locator('main [class*="largeTitle"]').first().waitFor({ timeout: 20_000 }).catch(() => undefined);
        await page.waitForLoadState('networkidle').catch(() => undefined);
        const m = await page.evaluate(() => {
          const sw = document.documentElement.scrollWidth;
          const iw = window.innerWidth;
          let worst = '';
          let max = 0;
          document.querySelectorAll('main *').forEach((el) => {
            const r = (el as HTMLElement).getBoundingClientRect();
            if (r.right > iw + 1 && r.right > max) {
              max = r.right;
              worst = `${el.tagName.toLowerCase()}.${String((el as HTMLElement).className).slice(0, 60)}`;
            }
          });
          return {
            sw,
            iw,
            worst,
            max: Math.round(max),
            tables: document.querySelectorAll('main table, main .mantine-Table-table').length,
            mobileTitle: document.querySelectorAll('main [class*="largeTitle"]').length,
          };
        });
        out.push({ viewport: vp.name, route, ...m });
        expect(m.sw, `overflow de pagina em ${route} (${m.worst})`).toBeLessThanOrEqual(m.iw);
        expect(m.tables, `<table> em ${route}`).toBe(0);
        expect(m.mobileTitle, `tela mobile (titulo grande) em ${route}`).toBeGreaterThan(0);
        if (vp.name === '390') {
          const slug = route.replace(/^\/|\/$/g, '');
          const name = slug.replace(/\//g, '_');
          await page.waitForTimeout(1800); // fim da animacao dos graficos
          await page.screenshot({ path: `${SHOTS_DIR}/${name}.png` });
          if (FULL_PAGE.has(slug)) await page.screenshot({ path: `${SHOTS_DIR}/${name}_full.png`, fullPage: true });
        }
      }
      expect(pageErrors, `erros de pagina em ${vp.name}`).toEqual([]);
      const fs = await import('node:fs');
      fs.mkdirSync('test-results/mobile-layout', { recursive: true });
      fs.writeFileSync(`test-results/mobile-layout/evolucao-${vp.name}.json`, JSON.stringify(out, null, 1));
    });
  });
}
