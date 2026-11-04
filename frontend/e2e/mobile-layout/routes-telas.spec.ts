import { mkdirSync } from 'node:fs';
import { expect, test, type Page } from '@playwright/test';
import { FROZEN_NOW, installMockApi } from '../desktop-baseline/mock-api';
import { installAdminSession, installCheckinProposal, installTelasFixtures } from './telas-fixtures';

/**
 * Mobile v2, frente C: as telas migradas (treino, corpo, nutricao e conta)
 * montam no shell mobile em 320 e 390 px, sem scroll horizontal da pagina e sem
 * `<table>` (inclusive nos fluxos abertos: filtros, formulario de exercicio,
 * editor de refeicao, busca de alimentos). Dados realistas em telas-fixtures.ts.
 * Capturas em `MOBILE_V2_C_SHOTS` (padrao: .omc/artifacts/mobile-v2/c).
 */
const ROUTES = [
  '/registro-exercicios/',
  '/registro-exercicios/1/',
  '/exercicios/',
  '/planos-treino/',
  '/planos-treino/1/',
  '/nutricao/',
  '/nutricao/plano/',
  '/nutricao/checkin/',
  '/nutricao/alimentos/',
  '/nutricao/favoritos/',
  '/indicadores-fisicos/',
  '/volume-grupamentos/',
  '/preferencias-treino/',
  '/perfil/',
  '/usuarios/',
];

const SHOTS = process.env.MOBILE_V2_C_SHOTS ?? 'C:/muscle_fit/.omc/artifacts/mobile-v2/c';
const VIEWPORTS = [
  { name: '320', width: 320, height: 800 },
  { name: '390', width: 390, height: 844 },
] as const;

async function check(page: Page, label: string, vp: string) {
  const m = await page.evaluate(() => ({
    sw: document.documentElement.scrollWidth,
    iw: window.innerWidth,
    tables: document.querySelectorAll('table, .mantine-Table-table').length,
  }));
  expect(m.sw, `overflow de pagina em ${label} (${vp})`).toBeLessThanOrEqual(m.iw);
  expect(m.tables, `<table> em ${label} (${vp})`).toBe(0);
  await page.screenshot({ path: `${SHOTS}/${vp}${label.replace(/[^a-z0-9]+/gi, '_')}.png` });
}

async function open(page: Page, route: string) {
  await page.goto(route);
  await page.locator('[data-mobile-shell="true"]').waitFor({ timeout: 20_000 });
  await page.waitForLoadState('networkidle').catch(() => undefined);
}

for (const vp of VIEWPORTS) {
  test.describe(`telas da frente C em ${vp.name}`, () => {
    test.use({ viewport: { width: vp.width, height: vp.height } });

    let unhandled: string[] = [];

    test.beforeEach(async ({ page }) => {
      mkdirSync(SHOTS, { recursive: true });
      await page.clock.setFixedTime(FROZEN_NOW);
      unhandled = (await installMockApi(page)).unhandled;
      await installTelasFixtures(page);
      await page.addInitScript(() => window.localStorage.setItem('musclefit.flag.mobile-ui', 'on'));
    });

    test.afterEach(async ({}, info) => {
      // Rota nao mockada = teste frágil: as telas com dados realistas nao podem depender do 599 do mock base.
      if (/rotas migradas|indicadores: percorre|volume|plano nutricional|check-in|usda/.test(info.title)) return;
      const faltando = [...new Set(unhandled)].filter((k) => !k.startsWith('OPTIONS'));
      expect(faltando, `rotas nao mockadas em "${info.title}"`).toEqual([]);
    });

    test('rotas migradas: sem overflow de pagina e sem <table>', async ({ page }) => {
      test.setTimeout(400_000);
      for (const route of ROUTES) {
        await open(page, route);
        await check(page, route, vp.name);
      }
    });

    test('historico: detalhe do treino e filtros abertos', async ({ page }) => {
      test.setTimeout(120_000);
      await open(page, '/registro-exercicios/');
      await expect(page.getByText('Treino A - Peito e tríceps')).toBeVisible();
      await page.getByRole('button', { name: /Filtros/ }).click();
      await page.getByRole('dialog').waitFor();
      await page.waitForTimeout(600);
      await check(page, 'historico-filtros', vp.name);
      await page.keyboard.press('Escape');
      await page.getByText('Treino A - Peito e tríceps').click();
      await page.getByRole('dialog').waitFor();
      await page.waitForTimeout(600);
      await check(page, 'historico-detalhe-sheet', vp.name);
    });

    test('detalhe do treino com series unilaterais e formulario de lancamento', async ({ page }) => {
      test.setTimeout(120_000);
      await open(page, '/registro-exercicios/1/');
      await expect(page.getByText('Rosca unilateral com halter')).toBeVisible();
      await check(page, 'treino-detalhe', vp.name);
      await page.getByRole('button', { name: 'Lançar exercício' }).click();
      await expect(page.getByText('Séries')).toBeVisible();
      await check(page, 'treino-lancar-exercicio', vp.name);
    });

    test('plano de treino pronto: lista, linha em edicao', async ({ page }) => {
      test.setTimeout(120_000);
      await open(page, '/planos-treino/');
      await expect(page.getByText('Ficha A - Peito e tríceps')).toBeVisible();
      await check(page, 'planos-lista', vp.name);
      await open(page, '/planos-treino/1/');
      await expect(page.getByText('1. Supino reto com barra')).toBeVisible();
      await check(page, 'plano-detalhe', vp.name);
      await page.getByText('1. Supino reto com barra').click();
      await expect(page.getByText('Progressão e prescrição')).toBeVisible();
      await check(page, 'plano-linha-edicao', vp.name);
    });

    test('diario: editor de refeicao com busca de alimentos', async ({ page }) => {
      test.setTimeout(120_000);
      await open(page, '/nutricao/');
      await page.getByRole('button', { name: 'Adicionar refeição' }).click();
      await page.getByLabel('Buscar alimento').fill('arroz');
      await expect(page.getByText('Arroz, integral, cozido')).toBeVisible();
      await check(page, 'diario-editor-busca', vp.name);
    });

    test('indicadores fisicos: lancamento em lista e detalhe', async ({ page }) => {
      test.setTimeout(120_000);
      await open(page, '/indicadores-fisicos/');
      await expect(page.getByText('82,4 kg')).toBeVisible();
      await check(page, 'indicadores-lista', vp.name);
      await page.getByText('82,4 kg').click();
      await page.getByRole('dialog').waitFor();
      await page.waitForTimeout(600);
      await check(page, 'indicadores-detalhe', vp.name);
    });

    test('alimentos: busca no catalogo em lista', async ({ page }) => {
      test.setTimeout(120_000);
      await open(page, '/nutricao/alimentos/');
      await page.getByLabel('Buscar alimento').fill('arroz');
      await expect(page.getByText('Arroz, integral, cozido')).toBeVisible();
      await check(page, 'alimentos-busca', vp.name);
    });

    test('treino: serie unilateral E/D e PUT do exercicio editado', async ({ page }) => {
      test.setTimeout(120_000);
      await open(page, '/registro-exercicios/1/');
      await page.getByText('Rosca unilateral com halter', { exact: true }).click();
      await page.getByRole('dialog').waitFor();
      await expect(page.getByRole('dialog').getByText(/E\s*10/)).toBeVisible();
      await page.waitForTimeout(600);
      await check(page, 'treino-serie-unilateral', vp.name);
      await page.keyboard.press('Escape');
      await page.getByText('Supino reto com barra', { exact: true }).click();
      await page.getByRole('dialog').waitFor();
      await page.getByRole('button', { name: 'Editar', exact: true }).click();
      const put = page.waitForRequest((r) => r.method() === 'PUT' && r.url().endsWith('/exercises/logs/11'));
      await page.getByRole('button', { name: 'Salvar alterações' }).click();
      const body = JSON.parse((await put).postData() ?? '{}');
      expect(body.original_name).toBe('Supino reto com barra');
      expect(body.sets).toHaveLength(3);
      expect(body.gym_id).toBe(1);
    });

    test('plano: PUT da ficha com os exercicios', async ({ page }) => {
      test.setTimeout(120_000);
      await open(page, '/planos-treino/1/');
      await expect(page.getByText('1. Supino reto com barra')).toBeVisible();
      const put = page.waitForRequest((r) => r.method() === 'PUT' && r.url().endsWith('/workout-plans/1'));
      await page.getByRole('button', { name: 'Salvar alterações' }).click();
      const body = JSON.parse((await put).postData() ?? '{}');
      expect(body.name).toBe('Ficha A - Peito e tríceps');
      expect(body.exercises).toHaveLength(3);
      expect(body.template_id).toBe(7);
    });

    test('usuarios: admin lista, edita e reseta senha', async ({ page }) => {
      test.setTimeout(120_000);
      await installAdminSession(page);
      await open(page, '/usuarios/');
      await expect(page.getByText('bia', { exact: true })).toBeVisible();
      await check(page, 'usuarios-lista', vp.name);
      await page.getByText('bia', { exact: true }).click();
      await expect(page.getByText('Editar bia')).toBeVisible();
      await check(page, 'usuarios-edicao', vp.name);
      await page.getByRole('button', { name: 'Resetar senha' }).click();
      await page.getByRole('dialog').getByRole('button', { name: 'Resetar senha' }).click();
      await expect(page.getByText('Tmp-Abc12345')).toBeVisible();
      await check(page, 'usuarios-reset', vp.name);
    });

    test('usuarios: nao admin nao chama GET /users', async ({ page }) => {
      test.setTimeout(120_000);
      const calls: string[] = [];
      page.on('request', (r) => {
        if (r.method() === 'GET' && new URL(r.url()).pathname === '/users') calls.push(r.url());
      });
      await open(page, '/usuarios/');
      await expect(page.getByText('Acesso restrito a administradores.')).toBeVisible();
      expect(calls).toEqual([]);
      await check(page, 'usuarios-nao-admin', vp.name);
    });

    test('indicadores: percorre as 5 secoes', async ({ page }) => {
      test.setTimeout(120_000);
      await open(page, '/indicadores-fisicos/');
      const titulos = ['Bioimpedância', 'Medidas por fita', 'Recuperação (Intervals.icu)', 'Sono', 'Esforço (EPOC / PAI)'];
      for (const titulo of titulos) {
        await page.getByRole('textbox', { name: 'Seção' }).click();
        await page.getByRole('option', { name: titulo, exact: true }).click();
        await expect(page.getByRole('textbox', { name: 'Seção' })).toHaveValue(titulo);
        await check(page, `indicadores-secao-${titulo}`, vp.name);
      }
    });

    test('usuarios: desativar volta a lista e nao reativa ao salvar', async ({ page }) => {
      test.setTimeout(120_000);
      await installAdminSession(page);
      const puts: string[] = [];
      page.on('request', (r) => {
        if (r.method() === 'PUT' && r.url().includes('/users/2')) puts.push(r.url());
      });
      await open(page, '/usuarios/');
      await page.getByText('bia', { exact: true }).click();
      await page.getByRole('button', { name: 'Desativar usuário' }).click();
      const del = page.waitForRequest((r) => r.method() === 'DELETE' && r.url().endsWith('/users/2'));
      await page.getByRole('dialog').getByRole('button', { name: 'Desativar' }).click();
      await del;
      await expect(page.getByText('Editar bia')).toHaveCount(0);
      await expect(page.getByText('Novo usuário')).toBeVisible();
      expect(puts).toEqual([]);
    });

    test('voltar (goBack) fecha o lancamento e mantem o rascunho', async ({ page }) => {
      test.setTimeout(120_000);
      await open(page, '/registro-exercicios/1/');
      await page.getByRole('button', { name: 'Lançar exercício' }).click();
      await expect(page.getByText('Séries', { exact: true })).toBeVisible();
      await page.getByLabel('Repetições').fill('12');
      await page.goBack();
      await expect(page.getByRole('heading', { name: 'Lançar exercício' })).toHaveCount(0);
      expect(new URL(page.url()).pathname).toBe('/registro-exercicios/1/');
      await page.getByRole('button', { name: 'Lançar exercício' }).click();
      await expect(page.getByLabel('Repetições')).toHaveValue('12');
    });

    test('voltar (goBack) fecha a linha do plano; fechar por codigo nao deixa entrada presa', async ({ page }) => {
      test.setTimeout(120_000);
      await open(page, '/planos-treino/');
      await page.getByText('Ficha A - Peito e tríceps').click();
      await page.getByRole('button', { name: 'Abrir plano' }).click();
      await expect(page.getByText('1. Supino reto com barra')).toBeVisible();
      expect(new URL(page.url()).pathname).toMatch(/^\/planos-treino\/1\/?$/);
      // goBack fecha a edicao da linha, sem sair da rota, e o digitado fica
      await page.getByText('1. Supino reto com barra').click();
      await page.getByLabel('Reps').fill('6-8');
      await page.goBack();
      await expect(page.getByText('1. Supino reto com barra')).toBeVisible();
      expect(new URL(page.url()).pathname).toMatch(/^\/planos-treino\/1\/?$/);
      await page.getByText('1. Supino reto com barra').click();
      await expect(page.getByLabel('Reps')).toHaveValue('6-8');
      // fechar por codigo (Concluir) desfaz a entrada: o proximo goBack sai da rota
      await page.getByRole('button', { name: 'Concluir' }).click();
      await expect(page.getByText('1. Supino reto com barra')).toBeVisible();
      await page.goBack();
      await expect.poll(() => new URL(page.url()).pathname).toMatch(/^\/planos-treino\/?$/);
    });

    test('voltar (goBack) fecha a geracao de plano sem sair da rota', async ({ page }) => {
      test.setTimeout(120_000);
      await open(page, '/planos-treino/');
      await page.getByRole('button', { name: 'Gerar novo plano' }).click();
      await page.getByLabel('Objetivo (opcional)').fill('foco em peito');
      await page.goBack();
      await expect(page.getByText('Ficha A - Peito e tríceps')).toBeVisible();
      expect(new URL(page.url()).pathname).toBe('/planos-treino/');
      await page.getByRole('button', { name: 'Gerar novo plano' }).click();
      await expect(page.getByLabel('Objetivo (opcional)')).toHaveValue('foco em peito');
    });

    test('lancamento novo: POST do exercicio e da serie', async ({ page }) => {
      test.setTimeout(120_000);
      await open(page, '/registro-exercicios/1/');
      await page.getByRole('button', { name: 'Lançar exercício' }).click();
      await page.getByRole('textbox', { name: 'Grupo muscular' }).click();
      await page.getByRole('option', { name: 'Peitoral' }).click();
      await page.getByRole('textbox', { name: 'Equipamento' }).click();
      await page.getByRole('option', { name: 'Banco reto' }).click();
      await page.getByRole('textbox', { name: 'Exercício canônico' }).click();
      await page.getByRole('option', { name: 'Supino reto com barra' }).click();
      const post = page.waitForRequest((r) => r.method() === 'POST' && r.url().endsWith('/workout-sessions/1/exercises'));
      const setPost = page.waitForRequest((r) => r.method() === 'POST' && r.url().endsWith('/workout-sessions/1/exercises/21/sets'));
      await page.getByRole('button', { name: 'Lançar exercício' }).click();
      const body = JSON.parse((await post).postData() ?? '{}');
      expect(body.original_name).toBe('Supino reto com barra');
      expect(body.mapping_id).toBe(1);
      expect(body.equipment_id).toBe(1);
      const setBody = JSON.parse((await setPost).postData() ?? '{}');
      expect(setBody).toMatchObject({ set_number: 1, reps: 10, weight_kg: 20 });
    });

    test('diario: POST da refeicao com item da busca (mealItems sem tabela)', async ({ page }) => {
      test.setTimeout(120_000);
      await open(page, '/nutricao/');
      await page.getByRole('button', { name: 'Adicionar refeição' }).click();
      await page.getByLabel('Buscar alimento').fill('arroz');
      await page.getByText('Arroz, integral, cozido').first().click();
      await page.getByLabel('Quantidade').first().fill('150');
      await page.getByRole('textbox', { name: 'Refeição' }).first().click();
      await page.getByRole('option').first().click();
      await expect(page.getByText('Itens')).toBeVisible();
      await page.waitForTimeout(800);
      await check(page, 'diario-refeicao-itens', vp.name);
      const post = page.waitForRequest((r) => r.method() === 'POST' && r.url().endsWith('/nutrition/meals'));
      await page.getByRole('button', { name: /Salvar/ }).first().click();
      const body = JSON.parse((await post).postData() ?? '{}');
      expect(body.local_date).toBe('2026-10-14');
      expect(body.items?.[0]?.food_id).toBe(1);
    });

    test('alimentos: usda e porcoes em lista, sem tabela', async ({ page }) => {
      test.setTimeout(120_000);
      await open(page, '/nutricao/alimentos/');
      await page.getByLabel('Buscar no USDA (em inglês)').fill('rice');
      await page.getByRole('button', { name: 'Buscar no USDA' }).click();
      await expect(page.getByText('Rice, white, cooked')).toBeVisible();
      await check(page, 'alimentos-usda', vp.name);
      await page.getByLabel('Buscar alimento', { exact: true }).fill('arroz');
      await page.getByText('Arroz, integral, cozido').first().click();
      await expect(page.getByText('1 colher de servir')).toBeVisible();
      await page.getByText('1 colher de servir').scrollIntoViewIfNeeded();
      await check(page, 'alimentos-porcoes', vp.name);
    });

    test('check-in com proposta em lista', async ({ page }) => {
      test.setTimeout(120_000);
      await installCheckinProposal(page);
      await open(page, '/nutricao/checkin/');
      await expect(page.getByText('Dia sem treino')).toBeVisible();
      await check(page, 'checkin-proposta', vp.name);
    });

    test('volume: comparativo em lista e detalhe por toque', async ({ page }) => {
      test.setTimeout(120_000);
      await open(page, '/volume-grupamentos/');
      await expect(page.getByText('Peito').first()).toBeVisible();
      await page.getByText(/Comparativo das últimas 4 sessões por/).first().scrollIntoViewIfNeeded();
      await page.waitForTimeout(400);
      await check(page, 'volume-comparativo', vp.name);
      await page.getByRole('button', { name: /Peito/ }).first().click();
      await page.getByRole('dialog').waitFor();
      await page.waitForTimeout(600);
      await check(page, 'volume-comparativo-detalhe', vp.name);
    });

    test('plano nutricional: novo rascunho fixo', async ({ page }) => {
      test.setTimeout(120_000);
      await open(page, '/nutricao/plano/');
      await expect(page.getByRole('button', { name: 'Novo rascunho' })).toBeVisible();
      await check(page, 'nutricao-plano', vp.name);
    });
  });
}
