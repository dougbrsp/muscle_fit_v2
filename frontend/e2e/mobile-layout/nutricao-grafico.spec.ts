import { mkdirSync } from 'node:fs';
import { expect, test, type Page, type Route } from '@playwright/test';
import { FROZEN_NOW, installMockApi, MOCK_API_ORIGIN } from '../desktop-baseline/mock-api';
import { installTelasFixtures } from './telas-fixtures';

/**
 * Nutricao (nutricao-melhorias, rodada 1): "Meta x ingestao" no celular a 390 px com um macro em excesso
 * (barra na cor da marca e texto "Acima da meta", sem julgar) e outro sem meta (texto "Sem gráfico", sem barra
 * em 0%), sem scroll horizontal. Dia mockado; os numeros sao os que a API devolveria (o front so apresenta).
 * Capturas em `NUTRICAO_SHOTS` (padrao: .omc/artifacts/nutricao-melhorias).
 */
const SHOTS = process.env.NUTRICAO_SHOTS ?? 'C:/muscle_fit/.omc/artifacts/nutricao-melhorias';

const LINHA_BASE = { intake_complete: true, share: null, within_target: null, to_consume: null, excess: null, remaining: null };
const DIA = {
  user: { id: 1, username: 'ana', full_name: 'Ana Teste' },
  local_date: '2026-10-14',
  today: '2026-10-14',
  target: { kcal: 2000, frozen: false, backfilled: false, limitations: [] },
  target_action: 'keep',
  target_notices: [],
  meals: [],
  totals: null,
  balance: {
    rows: [
      { ...LINHA_BASE, field: 'energy_kcal', target: 2000, intake: 2300, share: 1.15, within_target: 2000, to_consume: 0, excess: 300, remaining: -300 },
      { ...LINHA_BASE, field: 'protein_g', target: null, intake: 120 },
      { ...LINHA_BASE, field: 'carbohydrates_g', target: 250, intake: 100, intake_complete: false },
      { ...LINHA_BASE, field: 'total_fat_g', target: 70, intake: 40, share: 0.57, within_target: 40, to_consume: 30, excess: 0, remaining: 30 },
    ],
    tdee_kcal: null,
    energy_balance_kcal: null,
    limitations: [],
  },
  day_log: null,
  training: { is_training_day: false, training_known: true, sessions: [], comparison: null },
};

async function installDia(page: Page) {
  await page.route(`${MOCK_API_ORIGIN}/nutrition/days/2026-10-14`, async (route: Route) => {
    if (route.request().method() === 'OPTIONS') {
      await route.fallback();
      return;
    }
    await route.fulfill({
      status: 200,
      headers: { 'access-control-allow-origin': route.request().headers()['origin'] ?? '*', 'access-control-allow-credentials': 'true' },
      contentType: 'application/json',
      body: JSON.stringify(DIA),
    });
  });
}

test.describe('nutricao: Meta x ingestao a 390 px', () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test('excesso sem julgar, sem meta e total incompleto sem barra, sem scroll horizontal', async ({ page }) => {
    test.setTimeout(90_000);
    mkdirSync(SHOTS, { recursive: true });
    await page.clock.setFixedTime(FROZEN_NOW);
    await installMockApi(page);
    await installTelasFixtures(page);
    await installDia(page);
    await page.addInitScript(() => window.localStorage.setItem('musclefit.flag.mobile-ui', 'on'));
    await page.goto('/nutricao/');
    await page.locator('[data-mobile-shell="true"]').waitFor({ timeout: 20_000 });
    await page.waitForLoadState('networkidle').catch(() => undefined);

    await expect(page.getByRole('heading', { name: 'Meta × ingestão' })).toBeVisible();
    await expect(page.getByText(/Acima da meta: .*300/)).toBeVisible();
    // Energia (excesso) e gordura (dentro da meta) desenham barra; proteina (sem meta) e carbo (total incompleto) nao.
    await expect(page.locator('[data-mobile-shell="true"] [role="progressbar"]')).toHaveCount(2);
    await expect(page.getByText('Sem gráfico: falta meta ou o total do dia está incompleto.')).toHaveCount(2);
    // A barra do excesso nao e vermelha: a tela nao julga passar da meta.
    const cores = await page.locator('[data-mobile-shell="true"] [role="progressbar"]').evaluateAll((els) =>
      els.map((el) => getComputedStyle(el).getPropertyValue('--progress-section-color')),
    );
    for (const cor of cores) expect(cor).not.toContain('red');

    const m = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, iw: window.innerWidth }));
    expect(m.sw, 'overflow horizontal').toBeLessThanOrEqual(m.iw);
    await page.screenshot({ path: `${SHOTS}/390_diario_meta_ingestao.png`, fullPage: true });
  });
});
