import { test } from '@playwright/test';
import { FROZEN_NOW, installMockApi } from '../desktop-baseline/mock-api';

/**
 * Sonda (nao e gate): mede overflow horizontal de cada rota autenticada no
 * shell mobile e guarda um print. Saida: test-results/mobile-layout/probe.json
 * Ligada por PROBE=1.
 */
const ROUTES = [
  '/dashboard/', '/dashboard/treinos/', '/dashboard/progressao/', '/dashboard/recovery/', '/dashboard/musculos/',
  '/dashboard/corpo/', '/dashboard/cardio/', '/dashboard/ciclo/', '/dashboard/contexto/', '/dashboard/checkin/',
  '/dashboard/engine/', '/dashboard/data-quality/', '/dashboard/relatorios/', '/registro-exercicios/', '/agente-cardio/',
  '/insights/', '/indicadores-fisicos/', '/nutricao/', '/intervals-icu/', '/exercicios/', '/volume-grupamentos/',
  '/planos-treino/', '/academias/', '/preferencias-treino/', '/equipamentos-exercicios/', '/dominio-exercicios/',
  '/regras-carga/', '/metas-ativacao/', '/modelos-treino/', '/usuarios/', '/perfil/',
];

test.skip(!process.env.PROBE, 'sonda manual (PROBE=1)');

test('sonda de overflow por rota', async ({ page }) => {
  test.setTimeout(600_000);
  const out: Record<string, unknown>[] = [];
  for (const width of [320, 390]) {
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
      const name = route.replace(/\//g, '_');
      await page.screenshot({ path: `e2e/mobile-layout/__screens__/probe${name}${width}.png` });
    }
  }
  const fs = await import('node:fs');
  fs.mkdirSync('test-results/mobile-layout', { recursive: true });
  fs.writeFileSync('test-results/mobile-layout/probe.json', JSON.stringify(out, null, 1));
});
