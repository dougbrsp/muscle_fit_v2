import { expect, test } from '@playwright/test';
import { FROZEN_NOW, installMockApi } from '../desktop-baseline/mock-api';

/**
 * Mobile v2, criterio 4: toda rota autenticada e utilizavel no shell mobile em
 * 320, 390 e 430 px (retrato) e 844x390 (paisagem de celular, com toque): o
 * shell monta e a PAGINA nao rola na horizontal. Rotas so-desktop (D1) tambem
 * entram: mostram a tela "Disponivel no computador". Grava um print por rota e
 * um JSON (test-results/mobile-layout/routes-<viewport>.json) com a contagem de
 * `<table>` do Mantine por rota (as frentes B e C devem zerar nas migradas).
 */
const ROUTES = [
  '/dashboard/', '/dashboard/?hub=evolucao', '/dashboard/treinos/', '/dashboard/progressao/',
  '/dashboard/recovery/', '/dashboard/musculos/', '/dashboard/corpo/', '/dashboard/cardio/',
  '/dashboard/nutricao/', '/dashboard/ciclo/', '/dashboard/contexto/', '/dashboard/checkin/',
  '/dashboard/engine/', '/dashboard/data-quality/', '/dashboard/relatorios/', '/registro-exercicios/',
  '/agente-cardio/', '/insights/', '/indicadores-fisicos/', '/nutricao/', '/intervals-icu/',
  '/exercicios/', '/volume-grupamentos/', '/planos-treino/', '/preferencias-treino/', '/usuarios/',
  '/perfil/',
  // so-desktop (D1)
  '/academias/', '/equipamentos-exercicios/', '/dominio-exercicios/', '/regras-carga/',
  '/metas-ativacao/', '/modelos-treino/',
];
const DESKTOP_ONLY = new Set([
  '/academias/', '/equipamentos-exercicios/', '/dominio-exercicios/', '/regras-carga/',
  '/metas-ativacao/', '/modelos-treino/',
]);

const VIEWPORTS = [
  { name: '320', width: 320, height: 800, touch: false },
  { name: '390', width: 390, height: 844, touch: false },
  { name: '430', width: 430, height: 932, touch: false },
  { name: '844x390-paisagem', width: 844, height: 390, touch: true },
] as const;

for (const vp of VIEWPORTS) {
  test.describe(`routes ${vp.name}`, () => {
    test.use({ hasTouch: vp.touch, isMobile: vp.touch });

    test(`todas as rotas em ${vp.name} sem overflow de pagina`, async ({ page }) => {
      test.setTimeout(900_000);
      const out: Record<string, unknown>[] = [];
      await page.setViewportSize({ width: vp.width, height: vp.height });
      await page.clock.setFixedTime(FROZEN_NOW);
      await installMockApi(page);
      await page.addInitScript(() => window.localStorage.setItem('musclefit.flag.mobile-ui', 'on'));
      for (const route of ROUTES) {
        await page.goto(route);
        await page.locator('[data-mobile-shell="true"]').waitFor({ timeout: 20_000 }).catch(() => undefined);
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
            desktopOnly: document.querySelectorAll('[data-desktop-only="true"]').length,
            text: (document.querySelector('main')?.textContent || '').slice(0, 60),
          };
        });
        out.push({ viewport: vp.name, route, ...m, overflow: m.sw > m.iw });
        expect(m.sw, `overflow de pagina em ${route}`).toBeLessThanOrEqual(m.iw);
        expect(await page.locator('[data-mobile-shell="true"]').count(), `shell em ${route}`).toBe(1);
        expect(m.desktopOnly, `tela so-desktop em ${route}`).toBe(DESKTOP_ONLY.has(route) ? 1 : 0);
        if (DESKTOP_ONLY.has(route) || route.startsWith('/dashboard/?')) {
          expect(m.tables, `<table> em ${route}`).toBe(0);
        }
        const name = route.replace(/[/?=]/g, '_');
        await page.screenshot({ path: `e2e/mobile-layout/__screens__/rota${name}${vp.name}.png` });
      }
      const fs = await import('node:fs');
      fs.mkdirSync('test-results/mobile-layout', { recursive: true });
      fs.writeFileSync(`test-results/mobile-layout/routes-${vp.name}.json`, JSON.stringify(out, null, 1));
    });
  });
}
