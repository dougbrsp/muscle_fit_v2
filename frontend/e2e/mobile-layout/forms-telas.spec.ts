import { mkdirSync } from 'node:fs';
import { expect, test, type Page } from '@playwright/test';
import { FROZEN_NOW, installMockApi } from '../desktop-baseline/mock-api';
import { installTelasFixtures, installTimedSession } from './telas-fixtures';

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
      await expect(page.getByRole('button', { name: 'Salvar perfil' }), 'um unico Salvar perfil').toHaveCount(1);
      // Um so primario laranja: Trocar senha e Novo dispositivo em estilo secundario.
      const bg = (name: string) => page.getByRole('button', { name }).evaluate((el) => getComputedStyle(el).backgroundColor);
      const primario = await bg('Salvar perfil');
      expect(await bg('Trocar senha')).not.toBe(primario);
      expect(await bg('Novo dispositivo')).not.toBe(primario);
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
      const rpe = serie.getByRole('textbox', { name: 'RPE (esforço)' });
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
      const mais = page.getByRole('button', { name: /Dor muscular e articular/ });
      await expect(mais.locator('svg'), 'chevron de expandir').toHaveCount(1);
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

    test('limpar campos numericos: a tela fica vazia e o payload vai null (RPE, RPE E/D, Reps E/D, Tempo, Repetições)', async ({ page }) => {
      test.setTimeout(120_000);
      // A barra fixa muda de lugar quando o teclado "fecha" (foco): so toca depois disso.
      const esperarTecladoFechado = () =>
        expect(page.locator('[data-mobile-shell="true"]')).toHaveAttribute('data-keyboard', 'closed');
      const limpar = async (campo: ReturnType<Page['getByRole']>) => {
        await campo.fill('');
        await campo.blur();
        await expect(campo).toHaveValue('');
        await esperarTecladoFechado();
      };
      // Series unilaterais (Reps E/D, RPE E/D).
      await open(page, '/registro-exercicios/1/');
      await page.getByText('Rosca unilateral com halter', { exact: true }).click();
      await page.getByRole('dialog').waitFor();
      await page.getByRole('button', { name: 'Editar', exact: true }).click();
      let serie = page.getByRole('region', { name: 'Série 1' });
      // Limpar todos os lados some com o layout E/D (`hasSides`): RPE D fica preenchido.
      for (const nome of ['Reps E', 'Reps D', 'RPE E']) await limpar(serie.getByRole('textbox', { name: nome }));
      let put = page.waitForRequest((r) => r.method() === 'PUT' && r.url().endsWith('/exercises/logs/12'));
      await page.getByRole('button', { name: 'Salvar alterações' }).click();
      let body = JSON.parse((await put).postData() ?? '{}');
      await expect(page.getByRole('heading', { name: 'Editar exercício' })).toHaveCount(0);
      await expect(page.getByRole('heading', { name: 'Editar exercício' })).toHaveCount(0);
      expect(body.sets[0]).toMatchObject({ reps_left: null, reps_right: null, rpe_left: null });

      // Serie comum: RPE e Repeticoes.
      await open(page, '/registro-exercicios/1/');
      await page.getByText('Supino reto com barra', { exact: true }).click();
      await page.getByRole('dialog').waitFor();
      await page.getByRole('button', { name: 'Editar', exact: true }).click();
      serie = page.getByRole('region', { name: 'Série 1' });
      await limpar(serie.getByRole('textbox', { name: 'RPE (esforço)' }));
      // Repetições tem piso (min 1): o campo pode voltar ao piso ao sair; o que importa e a tela e o payload concordarem.
      const reps = serie.getByRole('textbox', { name: 'Repetições' });
      await reps.fill('');
      await reps.blur();
      const mostrado = await reps.inputValue();
      await esperarTecladoFechado();
      put = page.waitForRequest((r) => r.method() === 'PUT' && r.url().endsWith('/exercises/logs/11'));
      await page.getByRole('button', { name: 'Salvar alterações' }).click();
      body = JSON.parse((await put).postData() ?? '{}');
      await expect(page.getByRole('heading', { name: 'Editar exercício' })).toHaveCount(0);
      expect(body.sets[0].rpe).toBeNull();
      expect(body.sets[0].reps ?? null, 'reps no payload = o que a tela mostrava').toBe(mostrado === '' ? null : Number(mostrado));

      // Serie por tempo e maximo de reps (Tempo e Repeticoes com piso).
      await installTimedSession(page);
      await open(page, '/registro-exercicios/1/');
      await page.getByText('Prancha isometrica', { exact: true }).click();
      await page.getByRole('dialog').waitFor();
      await page.getByRole('button', { name: 'Editar', exact: true }).click();
      serie = page.getByRole('region', { name: 'Série 1' });
      await limpar(serie.getByRole('textbox', { name: 'Tempo (s)' }));
      put = page.waitForRequest((r) => r.method() === 'PUT' && r.url().endsWith('/exercises/logs/13'));
      await page.getByRole('button', { name: 'Salvar alterações' }).click();
      body = JSON.parse((await put).postData() ?? '{}');
      await expect(page.getByRole('heading', { name: 'Editar exercício' })).toHaveCount(0);
      expect(body.sets[0].duration_seconds ?? null).toBeNull();

      await open(page, '/registro-exercicios/1/');
      await page.getByText('Flexao maximo', { exact: true }).click();
      await page.getByRole('dialog').waitFor();
      await page.getByRole('button', { name: 'Editar', exact: true }).click();
      serie = page.getByRole('region', { name: 'Série 1' });
      const maxReps = serie.getByRole('textbox', { name: 'Repetições' });
      await maxReps.fill('');
      await maxReps.blur();
      // Piso do maximo de reps (dono: MEASURE_LIMITS): nunca fica um valor que o payload nao leva.
      const piso = await maxReps.inputValue();
      await esperarTecladoFechado();
      put = page.waitForRequest((r) => r.method() === 'PUT' && r.url().endsWith('/exercises/logs/14'));
      await limpar(serie.getByRole('textbox', { name: 'Tempo (s)' }));
      await page.getByRole('button', { name: 'Salvar alterações' }).click();
      body = JSON.parse((await put).postData() ?? '{}');
      await expect(page.getByRole('heading', { name: 'Editar exercício' })).toHaveCount(0);
      expect(body.sets[0].reps ?? null).toBe(piso === '' ? null : Number(piso));
      expect(body.sets[0].duration_seconds ?? null).toBeNull();
    });

    test('folha de busca: campo de 16 px (sem zoom no iOS) e linhas desabilitadas com cadeado', async ({ page }) => {
      test.setTimeout(90_000);
      // 9 grupos: a folha passa do limiar e mostra a busca.
      await page.route('**/exercise-mapping-catalog/groups', async (route) => {
        if (route.request().method() === 'OPTIONS') return route.fallback();
        await route.fulfill({
          status: 200,
          headers: { 'access-control-allow-origin': route.request().headers()['origin'] ?? '*', 'access-control-allow-credentials': 'true' },
          contentType: 'application/json',
          body: JSON.stringify({ items: ['Peitoral', 'Costas', 'Ombros', 'Bíceps', 'Tríceps', 'Quadríceps', 'Posterior', 'Glúteos', 'Panturrilha'].map((g) => ({ primary_group: g })) }),
        });
      });
      await open(page, '/registro-exercicios/1/');
      await page.getByRole('button', { name: 'Lançar exercício' }).click();
      // Sem grupo escolhido, equipamento e exercicio sao linhas estaticas com cadeado.
      await expect(page.getByLabel('Indisponível')).toHaveCount(2);
      await expect(page.getByRole('button', { name: /Equipamento/ })).toHaveCount(0);
      await page.getByRole('button', { name: /Grupo muscular/ }).click();
      const busca = page.getByRole('dialog').getByRole('textbox', { name: /Buscar em/ });
      await expect(busca).toBeVisible();
      const fz = await busca.evaluate((el) => parseFloat(getComputedStyle(el).fontSize));
      expect(fz).toBeGreaterThanOrEqual(16);
    });

    test('voltar com campo focado: o estado de teclado nao fica preso (a barra nao muda de lugar no proximo toque)', async ({ page }) => {
      test.setTimeout(90_000);
      await open(page, '/registro-exercicios/1/');
      await page.getByRole('button', { name: 'Lançar exercício' }).click();
      await page.getByRole('textbox', { name: 'Repetições' }).click();
      await expect(page.locator('[data-mobile-shell="true"]')).toHaveAttribute('data-keyboard', 'open');
      await page.goBack();
      await expect(page.getByRole('heading', { name: 'Lançar exercício' })).toHaveCount(0);
      await expect(page.locator('[data-mobile-shell="true"]')).toHaveAttribute('data-keyboard', 'closed');
    });

    test('plano de treino: o fim da ficha fica acima de "Salvar alteracoes"', async ({ page }) => {
      test.setTimeout(90_000);
      await open(page, '/planos-treino/1/');
      await expect(page.getByText('3. Tríceps na polia')).toBeVisible();
      await expect(page.getByRole('heading', { name: 'Ativação muscular' })).toBeVisible();
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
      for (const n of [1, 2, 3, 4, 5]) {
        const b = (await page.getByRole('button', { name: new RegExp(`^${n} ${n === 1 ? 'estrela' : 'estrelas'} para Rosca direta em Halteres`) }).boundingBox())!;
        expect(b.width, `estrela ${n}`).toBeGreaterThanOrEqual(44);
        expect(b.height, `estrela ${n}`).toBeGreaterThanOrEqual(44);
      }
      await expect(page.getByRole('button', { name: /Limpar minha nota para Rosca direta/ })).toBeVisible();
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
      await expect(page.getByText('Energia da semana')).toBeHidden();
      await expect(page.getByText('Nenhum dia registrado')).toHaveCount(0);
      await expect(page.getByText('Semana sem registros')).toBeVisible();
      await expect(page.getByText('Gasto do dia (TDEE)')).toHaveCount(0);
      await expect(page.getByText('Balanço (ingestão − gasto)')).toHaveCount(0);
      const traco = await page.locator('main').getByText(/^-$/).count();
      expect(traco, 'nenhum traco solto no bloco').toBe(0);
      await shot(page, 'diario_meta_vazia', vp.name);
      // A barra fixa nao cobre o fim do Diario ("Treino do dia" e o que vem depois).
      await expectEndClearOfSticky(page, 'diario');
      await shot(page, 'diario_fim', vp.name);
    });
  });
}
