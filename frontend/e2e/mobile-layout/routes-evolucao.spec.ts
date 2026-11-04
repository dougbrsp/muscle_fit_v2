import { expect, test, type Page, type Route } from '@playwright/test';
import { FROZEN_NOW, installMockApi, MOCK_API_ORIGIN } from '../desktop-baseline/mock-api';
import { resolveFixture } from './evolucao-fixtures';

/**
 * Mobile v2, frente B (Evolucao e dashboards): as 18 rotas migradas montam a
 * TELA MOBILE (titulo grande do ScreenHeader) COM DADOS (fixtures so desta
 * spec, por cima do mock-api), mostram o conteudo esperado, nao rolam na
 * horizontal, nao renderizam <table> e nao chamam rota nao mockada. Tambem cobre
 * os fluxos de escrita (check-in, Intervals, job do Agente), troca de filtro e
 * erro 500. Capturas 390x844 (e pagina inteira das mais densas) em
 * .omc/artifacts/mobile-v2/b/ (fora do git).
 */
const ROUTES: Array<[string, RegExp]> = [
  ['/dashboard/treinos/', /Consistência/],
  ['/dashboard/progressao/', /Detalhe por exercício/],
  ['/dashboard/recovery/', /Avaliação combinada/],
  ['/dashboard/musculos/', /Posterior de coxa/],
  ['/dashboard/corpo/', /Sinal de recomposição/],
  ['/dashboard/cardio/', /Resumo do período/],
  ['/dashboard/nutricao/', /Aderência/],
  ['/dashboard/ciclo/', /Modelo ABC/],
  ['/dashboard/contexto/', /Álcool/],
  ['/dashboard/checkin/', /Disposição/],
  ['/dashboard/engine/', /Treino A/],
  ['/dashboard/data-quality/', /Séries sem RPE/],
  ['/dashboard/relatorios/', /Baixar relatório/],
  ['/dashboard/insights/', /O HRV caiu\?/],
  ['/dashboard/exercicios/1/', /Melhor carga/],
  ['/insights/', /Gerar insight/],
  ['/agente-cardio/', /Recuperação muscular/],
  ['/intervals-icu/', /Resumo do período/],
];

const VIEWPORTS = [
  { name: '320', width: 320, height: 800, touch: false },
  { name: '390', width: 390, height: 844, touch: false },
  { name: '430', width: 430, height: 932, touch: false },
  { name: '844x390-paisagem', width: 844, height: 390, touch: true },
] as const;

const FULL_PAGE = new Set(['dashboard/musculos', 'dashboard/cardio', 'dashboard/treinos', 'dashboard/progressao', 'intervals-icu']);
const SHOTS_DIR = 'C:/muscle_fit/.omc/artifacts/mobile-v2/b';

async function prepare(page: Page, width: number, height: number) {
  await page.setViewportSize({ width, height });
  await page.clock.setFixedTime(FROZEN_NOW);
  const { unhandled } = await installMockApi(page);
  // Fixtures realistas SO desta spec (por cima do mock-api, que nao muda); o que nao estiver aqui cai no mock-api.
  await page.route(`${MOCK_API_ORIGIN}/**`, async (route) => {
    const req = route.request();
    if (req.method() !== 'GET') return route.fallback();
    const payload = resolveFixture(`GET ${new URL(req.url()).pathname}`);
    if (payload === undefined) return route.fallback();
    return fulfillJson(route, payload);
  });
  await page.addInitScript(() => window.localStorage.setItem('musclefit.flag.mobile-ui', 'on'));
  const pageErrors: string[] = [];
  page.on('pageerror', (e) => pageErrors.push(e.message.slice(0, 200)));
  return { unhandled, pageErrors };
}

function fulfillJson(route: Route, payload: unknown, status = 200) {
  const req = route.request();
  return route.fulfill({
    status,
    contentType: 'application/json',
    headers: { 'access-control-allow-origin': req.headers()['origin'] ?? '*', 'access-control-allow-credentials': 'true' },
    body: JSON.stringify(payload),
  });
}

async function open(page: Page, route: string) {
  await page.goto(route);
  await page.locator('[data-mobile-shell="true"]').waitFor({ timeout: 20_000 });
  await page.locator('main [class*="largeTitle"]').first().waitFor({ timeout: 20_000 });
}

for (const vp of VIEWPORTS) {
  test.describe(`evolucao ${vp.name}`, () => {
    test.use({ hasTouch: vp.touch, isMobile: vp.touch });

    test(`rotas da Evolucao em ${vp.name}`, async ({ page }) => {
      test.setTimeout(600_000);
      const out: Record<string, unknown>[] = [];
      const { unhandled, pageErrors } = await prepare(page, vp.width, vp.height);
      for (const [route, expected] of ROUTES) {
        await open(page, route);
        await expect(page.locator('main').getByText(expected).first(), `conteudo de ${route}`).toBeVisible({ timeout: 20_000 });
        await page.waitForLoadState('networkidle').catch(() => undefined); // so espera a rede aquietar; o conteudo ja foi afirmado
        const m = await page.evaluate(() => {
          const sw = document.documentElement.scrollWidth;
          const iw = window.innerWidth;
          let worst = '';
          let max = 0;
          document.querySelectorAll('main *').forEach((el) => {
            // Internos de <svg> sao recortados pelo viewBox: so o proprio <svg> conta.
            if (el.closest('svg') && el.tagName.toLowerCase() !== 'svg') return;
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
        expect(m.worst, `elemento fora da largura em ${route} (${m.max}px)`).toBe('');
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
      expect(unhandled, `chamadas nao mockadas em ${vp.name}`).toEqual([]);
      expect(pageErrors, `erros de pagina em ${vp.name}`).toEqual([]);
      const fs = await import('node:fs');
      fs.mkdirSync('test-results/mobile-layout', { recursive: true });
      fs.writeFileSync(`test-results/mobile-layout/evolucao-${vp.name}.json`, JSON.stringify(out, null, 1));
    });
  });
}

test.describe('evolucao: escrita, filtros e erro (390)', () => {
  test('check-in: o POST sai com o escore escolhido', async ({ page }) => {
    await prepare(page, 390, 844);
    let body: Record<string, unknown> | null = null;
    await page.route(`${MOCK_API_ORIGIN}/daily-checkins`, async (route) => {
      if (route.request().method() !== 'POST') return route.fallback();
      body = route.request().postDataJSON();
      return fulfillJson(route, { status: 'updated', item: { id: 1, local_date: '2026-10-14', energy: 4, soreness: 3, stress: 2, mood: 4, notes: null, created_at: null, updated_at: null, answered: [], score_labels: {} } });
    });
    await open(page, '/dashboard/checkin/');
    await expect(page.getByText('Disposição').first()).toBeVisible();
    await page.getByRole('radiogroup', { name: 'Disposição' }).getByRole('radio', { name: '5' }).click();
    await page.getByRole('button', { name: /Salvar correção|Registrar/ }).click();
    await expect.poll(() => body?.energy, { message: 'POST /daily-checkins com energy=5' }).toBe(5);
  });

  test('Intervals: o PATCH de wellness leva so o campo alterado', async ({ page }) => {
    await prepare(page, 390, 844);
    let body: Record<string, unknown> | null = null;
    await page.route(new RegExp(`${MOCK_API_ORIGIN}/integrations/intervals/wellness/.+`), async (route) => {
      if (route.request().method() !== 'PATCH') return route.fallback();
      body = route.request().postDataJSON();
      return fulfillJson(route, { status: 'ok' });
    });
    await open(page, '/intervals-icu/');
    await page.getByRole('button', { name: /Forma/ }).first().click();
    await page.getByLabel(/FC de repouso/).fill('49');
    await page.getByRole('button', { name: 'Salvar' }).click();
    await expect.poll(() => body, { message: 'PATCH /integrations/intervals/wellness' }).toEqual({ resting_hr_bpm: 49 });
  });

  test('Agente de Cardio: job termina, mostra a recomendação e esconde "Aplicar no Modelo"', async ({ page }) => {
    await prepare(page, 390, 844);
    await page.route(`${MOCK_API_ORIGIN}/cardio-recommendations/generate`, (route) => fulfillJson(route, { job_id: 'j1', status: 'pending' }));
    await page.route(`${MOCK_API_ORIGIN}/jobs/j1`, (route) =>
      fulfillJson(route, { status: 'done', result: { id: 9, reference_date: '2026-10-14', recommendation_markdown: '## Plano\n\n- 30 min em Z2', prompt_version: null } }),
    );
    await open(page, '/agente-cardio/');
    await page.getByRole('button', { name: 'Gerar recomendação' }).click();
    await expect(page.getByText(/Recomendação gerada/)).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText('Aplicar no Modelo')).toHaveCount(0);
  });

  test('troca de período refaz a consulta com a janela escolhida', async ({ page }) => {
    await prepare(page, 390, 844);
    const windows: string[] = [];
    page.on('request', (r) => {
      const u = new URL(r.url());
      if (u.pathname === '/analytics/dashboard/recovery-signals') windows.push(u.searchParams.get('window') ?? '');
    });
    await open(page, '/dashboard/recovery/');
    await expect(page.getByText('Avaliação combinada')).toBeVisible();
    await page.getByRole('radio', { name: '7 dias' }).click();
    await expect.poll(() => windows, { message: 'consulta com window=7d' }).toContain('7d');
  });

  test('erro 500 mostra o estado de erro com "Tentar de novo"', async ({ page }) => {
    await prepare(page, 390, 844);
    await page.route(`${MOCK_API_ORIGIN}/analytics/dashboard-cycle`, (route) => fulfillJson(route, { detail: 'e2e: falha' }, 500));
    await open(page, '/dashboard/ciclo/');
    await expect(page.getByRole('button', { name: 'Tentar de novo' })).toBeVisible({ timeout: 30_000 });
  });
  test('Cardio: PAI aparece sem wellness; semanas por classe e medidas no treino existem', async ({ page }) => {
    await prepare(page, 390, 844);
    const cardio = JSON.parse(JSON.stringify(resolveFixture('GET /analytics/dashboard/cardio')));
    cardio.wellness.points = [];
    await page.route(`${MOCK_API_ORIGIN}/analytics/dashboard/cardio`, (route) => fulfillJson(route, cardio));
    await open(page, '/dashboard/cardio/');
    await expect(page.getByText('PAI 7 dias (estimado)').first()).toBeVisible();
    await expect(page.getByText(/último em/)).toBeVisible();
    await page.getByRole('button', { name: /Todas as semanas/ }).click();
    await expect(page.getByText(/sem zona \d+ \(\d+ min\)/).first()).toBeVisible();
    await page.keyboard.press('Escape');
    await page.getByRole('button', { name: /FC por sessão de treino/ }).click();
    await expect(page.getByText(/medidas? no treino/).first()).toBeVisible();
  });

  test('Grupos musculares: gráfico por período com escolha de até 3 grupos', async ({ page }) => {
    await prepare(page, 390, 844);
    await open(page, '/dashboard/musculos/');
    await page.getByRole('button', { name: /Gráfico por período/ }).click();
    await expect(page.getByRole('checkbox').first()).toBeVisible();
    await expect(page.getByRole('img', { name: 'Tendência por grupo muscular' })).toBeVisible();
    expect(await page.getByRole('checkbox', { checked: true }).count()).toBe(3);
  });

  test('Motor: folha da geração traz limites, trocas, IA, modo e origem, inclusive rascunho sem ficha', async ({ page }) => {
    await prepare(page, 390, 844);
    await open(page, '/dashboard/engine/');
    await page.getByRole('button', { name: /Rascunho não salvo/ }).click();
    for (const t of ['Limites de segurança aplicados', 'Trocas', 'Caminhos alternativos', 'IA usada', 'Modo de volume', 'Origem do registro', 'Esta geração não virou ficha']) {
      await expect(page.getByText(t).first()).toBeVisible();
    }
    await expect(page.getByText('Orçamento por grupo muscular').first()).toBeVisible();
  });

  test('Exercício: 1RM não confiável mostra o aviso no destaque', async ({ page }) => {
    await prepare(page, 390, 844);
    const ex = JSON.parse(JSON.stringify(resolveFixture('GET /analytics/dashboard/exercises/1/progress')));
    ex.exposures[ex.exposures.length - 1].e1rm_reliable = false;
    await page.route(new RegExp(`${MOCK_API_ORIGIN}/analytics/dashboard/exercises/1/progress`), (route) => fulfillJson(route, ex));
    await open(page, '/dashboard/exercicios/1/');
    await expect(page.getByText('pouco confiável').first()).toBeVisible();
  });
});
