import { expect, test, type Page } from '@playwright/test';
import { FROZEN_NOW, installMockApi } from './mock-api';

/**
 * Regressao visual do DESKTOP nas rotas criticas (viewports 1280 e 1440, ver
 * `playwright.config.ts`). Contra o export estatico, API mockada, relogio
 * congelado. Nao testa comportamento: so garante que o desktop nao mudou.
 */
type RouteCase = {
  name: string;
  path: string;
  anonymous?: boolean;
  /** Texto que prova que a tela terminou de carregar (nao o loader). */
  ready: string | RegExp;
};

const ROUTES: RouteCase[] = [
  { name: 'login', path: '/', anonymous: true, ready: /entrar/i },
  { name: 'home-dashboard', path: '/dashboard/', ready: /voce concluiu 10 de 12|pronto para treinar/i },
  { name: 'modo-treino', path: '/modo-treino/', ready: /./ },
  { name: 'historico', path: '/dashboard/treinos/', ready: /costas e biceps/i },
  { name: 'biblioteca-exercicios', path: '/exercicios/', ready: /./ },
  { name: 'planos-treino', path: '/planos-treino/', ready: /treino a - peito/i },
  { name: 'perfil', path: '/perfil/', ready: /ana teste|ana@example/i },
];

async function settle(page: Page) {
  await page.waitForLoadState('networkidle');
  await page.evaluate(() => document.fonts.ready);
}

for (const route of ROUTES) {
  test(`desktop: ${route.name}`, async ({ page }) => {
    const pageErrors: string[] = [];
    page.on('pageerror', (error) => pageErrors.push(error.message));

    await page.clock.setFixedTime(FROZEN_NOW);
    const mock = await installMockApi(page, { anonymous: route.anonymous });

    await page.goto(route.path);
    await expect(page.getByText(route.ready).first()).toBeVisible({ timeout: 20_000 });
    await settle(page);

    // Recharts anima via JS e no WebKit o quadro final oscila entre execucoes:
    // o CONTEUDO dos graficos fica mascarado (a caixa/layout continua comparada).
    await expect(page).toHaveScreenshot(`${route.name}.png`, {
      fullPage: true,
      mask: [page.locator('.recharts-wrapper')],
    });

    expect(mock.unhandled, 'chamadas de API nao mockadas').toEqual([]);
    expect(pageErrors, 'erros de pagina').toEqual([]);
  });
}
