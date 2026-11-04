import { mkdirSync } from 'node:fs';
import { expect, test, type Page } from '@playwright/test';
import { FROZEN_NOW, installMockApi } from '../desktop-baseline/mock-api';
import { installTelasFixtures } from './telas-fixtures';

/**
 * Mobile v2, acabamento dos formularios (Perfil, Lancar exercicio, Preferencias
 * de treino, Diario) e folga inferior de toda tela com `StickyAction`.
 * Capturas em `MOBILE_V2_D_SHOTS` (padrao: .omc/artifacts/mobile-v2/d).
 */
const SHOTS = process.env.MOBILE_V2_D_SHOTS ?? 'C:/muscle_fit/.omc/artifacts/mobile-v2/d';
const VIEWPORTS = [
  { name: '320', width: 320, height: 800 },
  { name: '390', width: 390, height: 844 },
] as const;

async function open(page: Page, route: string) {
  await page.goto(route);
  await page.locator('[data-mobile-shell="true"]').waitFor({ timeout: 20_000 });
  await page.waitForLoadState('networkidle').catch(() => undefined);
}

async function shot(page: Page, label: string, vp: string, fullPage = false) {
  const m = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, iw: window.innerWidth, tables: document.querySelectorAll('table').length }));
  expect(m.sw, `overflow de pagina em ${label} (${vp})`).toBeLessThanOrEqual(m.iw);
  expect(m.tables, `<table> em ${label} (${vp})`).toBe(0);
  await page.screenshot({ path: `${SHOTS}/${vp}_${label}.png`, fullPage });
}

/** Rola ao fim e confere que o espaco reservado cobre a barra fixa: o fim do conteudo fica acima dela. */
async function expectEndClearOfSticky(page: Page, label: string) {
  await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
  await page.waitForTimeout(200);
  const m = await page.evaluate(() => {
    const bar = document.querySelector<HTMLElement>('[data-sticky-action]');
    const main = document.querySelector('main');
    if (!bar || !main) return null;
    // Maior borda inferior de qualquer elemento visivel da tela (o fim real do conteudo).
    let contentBottom = 0;
    for (const el of Array.from(main.querySelectorAll<HTMLElement>('*'))) {
      if (bar.contains(el)) continue;
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) contentBottom = Math.max(contentBottom, r.bottom);
    }
    const b = bar.getBoundingClientRect();
    return { barTop: b.top, barH: b.height, contentBottom };
  });
  expect(m, `StickyAction presente em ${label}`).not.toBeNull();
  expect(m!.contentBottom, `fim do conteudo acima da barra em ${label}`).toBeLessThanOrEqual(m!.barTop + 0.5);
}

for (const vp of VIEWPORTS) {
  test.describe(`formularios da frente C em ${vp.name}`, () => {
    test.use({ viewport: { width: vp.width, height: vp.height } });

    test.beforeEach(async ({ page }) => {
      mkdirSync(SHOTS, { recursive: true });
      await page.clock.setFixedTime(FROZEN_NOW);
      await installMockApi(page);
      await installTelasFixtures(page);
      await page.addInitScript(() => window.localStorage.setItem('musclefit.flag.mobile-ui', 'on'));
    });

    test('perfil: secoes, foto separada do selo, salvar fixo sem cobrir campo e PUT inalterado', async ({ page }) => {
      test.setTimeout(90_000);
      await open(page, '/perfil/');
      for (const titulo of ['Dados pessoais', 'Preferências', 'Plano nutricional', 'Cardio', 'Intervals.icu']) {
        await expect(page.getByRole('heading', { name: titulo })).toBeVisible();
      }
      const badge = page.getByText('Usuário', { exact: true }).first();
      const trocar = page.getByRole('button', { name: 'Trocar foto' });
      const bb = (await badge.boundingBox())!;
      const tb = (await trocar.boundingBox())!;
      expect(tb.y, 'Trocar foto abaixo do selo, nao colado').toBeGreaterThan(bb.y + bb.height);
      expect(tb.height).toBeGreaterThanOrEqual(44);
      await shot(page, 'perfil_topo', vp.name);
      await expect(page.getByLabel('Nome completo')).toBeVisible();
      await expectEndClearOfSticky(page, 'perfil');
      await shot(page, 'perfil_fim', vp.name);
      const put = page.waitForRequest((r) => r.method() === 'PUT' && r.url().endsWith('/users/me'));
      await page.getByRole('button', { name: 'Salvar perfil' }).click();
      const body = JSON.parse((await put).postData() ?? '{}');
      expect(body).toMatchObject({ preferred_weight_unit: expect.any(String), timezone: expect.any(String) });
      expect(Object.keys(body)).toEqual(expect.arrayContaining(['full_name', 'email', 'birth_date', 'intervals_athlete_id']));
    });

    test('perfil: troque-senha=1 continua (aviso e cartao de senha)', async ({ page }) => {
      test.setTimeout(90_000);
      await open(page, '/perfil/?troque-senha=1');
      await expect(page.getByText('Troca de senha obrigatória')).toBeVisible();
    });

    test('lancar exercicio: seletores em folha, primeira serie sem rolar, steppers de 44 pt e payload', async ({ page }) => {
      test.setTimeout(90_000);
      await open(page, '/registro-exercicios/1/');
      await page.getByRole('button', { name: 'Lançar exercício' }).click();
      await expect(page.getByText('Séries')).toBeVisible();

      // Placeholder e valor sem truncar: a linha mostra o texto inteiro.
      const grupo = page.getByRole('button', { name: /Grupo muscular/ });
      await grupo.click();
      await shot(page, 'lancar_folha_grupo', vp.name);
      await page.getByRole('dialog').getByRole('button', { name: 'Peitoral' }).click();
      await page.getByRole('button', { name: /Equipamento/ }).click();
      await page.getByRole('dialog').getByRole('button', { name: 'Banco reto' }).click();
      await page.getByRole('button', { name: /Exercício canônico/ }).click();
      await page.getByRole('dialog').getByRole('button', { name: 'Supino reto com barra' }).click();
      await expect(page.getByRole('button', { name: /Supino reto com barra.*Exercício canônico/ })).toBeVisible();

      // Primeira serie visivel sem rolar (viewport do iPhone) e RPE acima da barra fixa.
      const serie = page.getByRole('region', { name: 'Série 1' });
      await expect(serie).toBeVisible();
      const bar = page.getByRole('button', { name: 'Lançar exercício' });
      const rpe = serie.getByLabel('RPE', { exact: true });
      const sb = (await serie.boundingBox())!;
      const barBox = (await bar.boundingBox())!;
      if (vp.height >= 844) {
        const rb = (await rpe.boundingBox())!;
        expect(rb.y + rb.height, 'RPE acima da barra fixa sem rolar').toBeLessThanOrEqual(barBox.y);
      }
      expect(sb.y).toBeLessThan(barBox.y);

      for (const nome of ['Diminuir Repetições', 'Aumentar Repetições', 'Diminuir Carga (kg)', 'Aumentar Carga (kg)']) {
        const box = (await serie.getByRole('button', { name: nome }).boundingBox())!;
        expect(box.width, nome).toBeGreaterThanOrEqual(44);
        expect(box.height, nome).toBeGreaterThanOrEqual(44);
      }
      await serie.getByRole('button', { name: 'Aumentar Repetições' }).click();
      await serie.getByRole('button', { name: 'Aumentar Carga (kg)' }).click();
      await shot(page, 'lancar_serie', vp.name);
      await expectEndClearOfSticky(page, 'lancar');

      const post = page.waitForRequest((r) => r.method() === 'POST' && r.url().endsWith('/workout-sessions/1/exercises'));
      const setPost = page.waitForRequest((r) => r.method() === 'POST' && r.url().endsWith('/workout-sessions/1/exercises/21/sets'));
      await bar.click();
      const body = JSON.parse((await post).postData() ?? '{}');
      expect(body.original_name).toBe('Supino reto com barra');
      const setBody = JSON.parse((await setPost).postData() ?? '{}');
      // 10 + 1 repeticao e 20 + 0,5 kg (passo do campo do desktop); carga sempre total.
      expect(setBody).toMatchObject({ set_number: 1, reps: 11, weight_kg: 20.5 });
    });

    test('plano de treino: o fim da ficha fica acima de "Salvar alteracoes"', async ({ page }) => {
      test.setTimeout(90_000);
      await open(page, '/planos-treino/1/');
      await expect(page.getByText('3. Tríceps na polia')).toBeVisible();
      await expectEndClearOfSticky(page, 'plano');
      await page.getByText('3. Tríceps na polia').scrollIntoViewIfNeeded();
      const item = (await page.getByText('3. Tríceps na polia').boundingBox())!;
      const save = (await page.getByRole('button', { name: 'Salvar alterações' }).boundingBox())!;
      expect(item.y + item.height, 'terceiro exercicio visivel acima da barra apos rolar').toBeLessThanOrEqual(save.y);
      await shot(page, 'plano_detalhe_fim', vp.name);
    });

    test('preferencias de treino: sem icone-placeholder, estrelas e limpar de 44 pt, ajuda em rodape', async ({ page }) => {
      test.setTimeout(90_000);
      await open(page, '/preferencias-treino/');
      await expect(page.getByText('Rosca direta')).toBeVisible();
      expect(await page.locator('.tabler-icon-photo').count(), 'sem placeholder de foto por linha').toBe(0);
      const estrela = page.getByRole('button', { name: /^5 estrelas para Rosca direta em Halteres/ });
      const box = (await estrela.boundingBox())!;
      expect(box.width).toBeGreaterThanOrEqual(44);
      expect(box.height).toBeGreaterThanOrEqual(44);
      const limpar = page.getByRole('button', { name: /Limpar minha nota para Supino reto com barra/ });
      const lb = (await limpar.boundingBox())!;
      expect(lb.width).toBeGreaterThanOrEqual(44);
      expect(lb.height).toBeGreaterThanOrEqual(44);
      // A explicacao longa vira rodape do grupo de filtros (nao subtitulo do cabecalho).
      await expect(page.getByText(/sem nota conta como 3/i)).toBeVisible();
      await shot(page, 'preferencias', vp.name);
      const put = page.waitForRequest((r) => r.method() === 'PUT' && /\/me\/equipment-exercise-preferences\/2$/.test(r.url()));
      await estrela.click();
      expect(JSON.parse((await put).postData() ?? '{}')).toEqual({ stars: 5 });
    });

    test('diario: "Meta x ingestao" sem dado vira um estado vazio unico, sem fileira de tracos', async ({ page }) => {
      test.setTimeout(90_000);
      await open(page, '/nutricao/');
      await expect(page.getByRole('heading', { name: 'Meta × ingestão' })).toBeVisible();
      await expect(page.getByText('Sem meta nem refeições neste dia.')).toBeVisible();
      await expect(page.getByText('Gasto do dia (TDEE)')).toHaveCount(0);
      await expect(page.getByText('Balanço (ingestão − gasto)')).toHaveCount(0);
      const traco = await page.locator('main').getByText(/^-$/).count();
      expect(traco, 'nenhum traco solto no bloco').toBe(0);
      await shot(page, 'diario_meta_vazia', vp.name);
    });
  });
}
