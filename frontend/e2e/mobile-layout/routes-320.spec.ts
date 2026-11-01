import { expect, test } from '@playwright/test';
import { FROZEN_NOW, installMockApi } from '../desktop-baseline/mock-api';

/**
 * A3: toda rota autenticada do menu e utilizavel no shell mobile em 320 px:
 * o shell monta e a PAGINA nao rola na horizontal (tabelas largas rolam dentro
 * do proprio container). Tambem grava um print e um JSON (test-results/mobile-layout/routes-320.json).
 */
const ROUTES = [
  '/dashboard/', '/dashboard/treinos/', '/dashboard/progressao/', '/dashboard/recovery/', '/dashboard/musculos/',
  '/dashboard/corpo/', '/dashboard/cardio/', '/dashboard/ciclo/', '/dashboard/contexto/', '/dashboard/checkin/',
  '/dashboard/engine/', '/dashboard/data-quality/', '/dashboard/relatorios/', '/registro-exercicios/', '/agente-cardio/',
  '/insights/', '/indicadores-fisicos/', '/nutricao/', '/intervals-icu/', '/exercicios/', '/volume-grupamentos/',
  '/planos-treino/', '/academias/', '/preferencias-treino/', '/equipamentos-exercicios/', '/dominio-exercicios/',
  '/regras-carga/', '/metas-ativacao/', '/modelos-treino/', '/usuarios/', '/perfil/',
];

test('A3: todas as rotas do menu em 320 sem overflow de pagina', async ({ page }) => {
  test.setTimeout(600_000);
  const out: Record<string, unknown>[] = [];
  for (const width of [320]) {
    await page.setViewportSize({ width, height: 800 });
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
        return { sw, iw, worst, max: Math.round(max), text: (document.querySelector('main')?.textContent || '').slice(0, 60) };
      });
      out.push({ width, route, ...m, overflow: m.sw > m.iw });
      expect(m.sw, `overflow de pagina em ${route}`).toBeLessThanOrEqual(m.iw);
      expect(await page.locator('[data-mobile-shell="true"]').count(), `shell em ${route}`).toBe(1);
      const name = route.replace(/\//g, '_');
      await page.screenshot({ path: `e2e/mobile-layout/__screens__/rota${name}${width}.png` });
    }
  }
  const fs = await import('node:fs');
  fs.mkdirSync('test-results/mobile-layout', { recursive: true });
  fs.writeFileSync('test-results/mobile-layout/routes-320.json', JSON.stringify(out, null, 1));
});
