import { mkdirSync } from 'node:fs';
import { expect, test, type Page } from '@playwright/test';
import { FROZEN_NOW, installMockApi } from '../desktop-baseline/mock-api';
import { installAdminSession, installTelasFixtures } from './telas-fixtures';

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
      if (/rotas migradas|indicadores: percorre/.test(info.title)) return;
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
  });
}
