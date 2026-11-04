import { expect, test, type Page } from '@playwright/test';
import {
  FROZEN_NOW,
  MOCK_API_ORIGIN,
  SESSION_USER,
  installMockApi,
} from '../desktop-baseline/mock-api';

/**
 * Layout mobile (mobile v2, frente A): seletor atras da flag, shell com tab bar
 * de 5 abas, hub Evolucao, Mais agrupada, secoes so-desktop (D1), paisagem (D2),
 * teclado (MB-8), sem overflow horizontal, navegacao preservando URL.
 */
const PORTRAIT = [320, 375, 390, 414, 430] as const;
// Paisagem de celular: 667 e mobile por largura. 844x390 so e mobile COM toque
// (D2: pointer coarse + altura baixa); sem toque cai no desktop.
const LANDSCAPE = [{ width: 667, height: 375 }] as const;
const TABLET = [
  { width: 844, height: 390 },
  { width: 768, height: 1024 },
  { width: 1024, height: 768 },
] as const;
const FLAG_KEY = 'musclefit.flag.mobile-ui';
const SHELL = '[data-mobile-shell="true"]';
const READY = /voce concluiu 10 de 12|pronto para treinar/i;
// Evidencia visual (fora do versionamento): capturas 390x844 da frente A. Diretorio por
// variavel de ambiente, senao relativo ao teste (pasta `__screens__`, ignorada).
const ARTIFACTS = process.env.MOBILE_ARTIFACTS_DIR ?? 'e2e/mobile-layout/__screens__/artifacts';

const DESKTOP_ONLY_ROUTES = [
  '/academias/',
  '/equipamentos-exercicios/',
  '/dominio-exercicios/',
  '/regras-carga/',
  '/metas-ativacao/',
  '/metas-ativacao/1/',
  '/modelos-treino/',
  '/modelos-treino/1/',
  '/diagnostico-volume/1/',
  '/tipos-treinamento/',
];
const DESKTOP_ONLY_LABELS = [
  'Academias',
  'Equipamentos x Exercícios',
  'Domínio de Exercícios',
  'Regras de carga',
  'Metas de Ativação',
  'Modelos de treino',
];
const ROLES = ['user', 'personal_trainer', 'admin'] as const;
type Role = (typeof ROLES)[number];

/** Responde `/auth/me` com o papel pedido (registrada depois do mock-base, vence). */
async function asRole(page: Page, role: Role) {
  await page.route(`${MOCK_API_ORIGIN}/auth/me`, async (route) => {
    const origin = route.request().headers()['origin'] ?? '*';
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      headers: {
        'access-control-allow-origin': origin,
        'access-control-allow-credentials': 'true',
        'access-control-allow-headers': '*',
      },
      body: JSON.stringify({ ...SESSION_USER, role, intervals_configured: role === 'admin' }),
    });
  });
}

async function open(
  page: Page,
  path: string,
  flag: 'on' | 'off' | null,
  size: { width: number; height: number },
  role?: Role,
) {
  await page.setViewportSize(size);
  await page.clock.setFixedTime(FROZEN_NOW);
  await installMockApi(page);
  if (role) await asRole(page, role);
  if (flag) {
    await page.addInitScript(([k, v]) => window.localStorage.setItem(k, v), [FLAG_KEY, flag]);
  }
  await page.goto(path);
}

async function noHorizontalOverflow(page: Page) {
  const { scrollWidth, innerWidth } = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
  }));
  expect(scrollWidth, 'scrollWidth > largura da viewport').toBeLessThanOrEqual(innerWidth);
}

const sizes = [
  ...PORTRAIT.map((w) => ({ name: `${w}-retrato`, width: w, height: 800 })),
  ...LANDSCAPE.map((s) => ({ name: `${s.width}-paisagem`, ...s })),
];

for (const size of sizes) {
  test(`mobile ${size.name}: shell na mesma URL, sem overflow, alvos >= 44px`, async ({ page }) => {
    await open(page, '/dashboard/', 'on', size);
    await expect(page.locator(SHELL)).toBeVisible({ timeout: 20_000 });
    expect(new URL(page.url()).pathname).toBe('/dashboard/');
    await expect(page.getByText(READY).first()).toBeVisible({ timeout: 20_000 });
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: `e2e/mobile-layout/__screens__/dashboard-${size.name}.png` });

    await expect(page.getByRole('navigation', { name: 'Menu principal' })).toHaveCount(0);

    const nav = page.getByRole('navigation', { name: 'Navegação principal' });
    const tabs = nav.locator('a, button');
    await expect(tabs).toHaveCount(5);
    await expect(tabs).toHaveText(['Início', 'Treino', 'Histórico', 'Evolução', 'Mais']);
    for (let i = 0; i < 5; i += 1) {
      const box = await tabs.nth(i).boundingBox();
      expect(box!.height, `aba ${i} altura`).toBeGreaterThanOrEqual(44);
      expect(box!.width, `aba ${i} largura`).toBeGreaterThanOrEqual(44);
    }
    const navBox = await nav.boundingBox();
    expect(navBox!.x).toBeGreaterThanOrEqual(0);
    expect(navBox!.x + navBox!.width).toBeLessThanOrEqual(size.width);
    await noHorizontalOverflow(page);
  });
}

test('captura da tab bar e do Início em 390x844', async ({ page }) => {
  await open(page, '/dashboard/', 'on', { width: 390, height: 844 });
  await expect(page.getByText(READY).first()).toBeVisible({ timeout: 20_000 });
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: `${ARTIFACTS}/inicio-390x844.png` });
  await page
    .getByRole('navigation', { name: 'Navegação principal' })
    .screenshot({ path: `${ARTIFACTS}/tab-bar-390x844.png` });
});

for (const size of TABLET) {
  test(`tablet/desktop ${size.width}x${size.height} (sem toque): cai no desktop mesmo com a flag ligada`, async ({ page }) => {
    await open(page, '/dashboard/', 'on', size);
    await expect(page.getByRole('navigation', { name: 'Menu principal' })).toBeVisible({ timeout: 20_000 });
    await expect(page.locator(SHELL)).toHaveCount(0);
  });
}

// D2: celular = toque + menor lado da TELA fisica < 500 px (nao depende da janela).
const DEVICES = [
  { name: 'iPhone 844x390', screen: { width: 844, height: 390 }, viewport: { width: 844, height: 390 }, touch: true, mobile: true },
  { name: 'iPhone Pro Max 956x440', screen: { width: 956, height: 440 }, viewport: { width: 956, height: 440 }, touch: true, mobile: true },
  { name: 'celular com teclado aberto (janela 844x300)', screen: { width: 844, height: 390 }, viewport: { width: 844, height: 300 }, touch: true, mobile: true },
  { name: 'desktop 1024x768 sem toque', screen: { width: 1024, height: 768 }, viewport: { width: 1024, height: 768 }, touch: false, mobile: false },
  { name: 'tablet 820x1180 com toque', screen: { width: 820, height: 1180 }, viewport: { width: 820, height: 1180 }, touch: true, mobile: false },
  { name: 'tablet 1024x600 com toque', screen: { width: 1024, height: 600 }, viewport: { width: 1024, height: 600 }, touch: true, mobile: false },
  { name: 'desktop com tela de toque e janela baixa', screen: { width: 1920, height: 1080 }, viewport: { width: 1000, height: 400 }, touch: true, mobile: false },
] as const;

for (const d of DEVICES) {
  test(`D2: ${d.name} => ${d.mobile ? 'layout mobile' : 'layout desktop'}`, async ({ browser }) => {
    const ctx = await browser.newContext({
      viewport: d.viewport,
      screen: d.screen,
      hasTouch: d.touch,
      isMobile: d.touch,
      locale: 'pt-BR',
      timezoneId: 'America/Sao_Paulo',
      colorScheme: 'dark',
      serviceWorkers: 'block',
    });
    const page = await ctx.newPage();
    await page.clock.setFixedTime(FROZEN_NOW);
    await installMockApi(page);
    await page.addInitScript(([k, v]) => window.localStorage.setItem(k, v), [FLAG_KEY, 'on']);
    await page.goto('/dashboard/');
    // prova que o contexto emula o que o caso diz: toque => pointer coarse
    expect(await page.evaluate(() => window.matchMedia('(pointer: coarse)').matches), 'pointer coarse').toBe(d.touch);
    if (d.mobile) {
      await expect(page.locator(SHELL)).toBeVisible({ timeout: 20_000 });
      await expect(page.getByRole('navigation', { name: 'Menu principal' })).toHaveCount(0);
      const tabs = page.getByRole('navigation', { name: 'Navegação principal' }).locator('a, button');
      await expect(tabs).toHaveCount(5);
      await noHorizontalOverflow(page);
      if (d.name === 'iPhone 844x390') await page.screenshot({ path: `${ARTIFACTS}/inicio-paisagem-844x390.png` });
    } else {
      await expect(page.getByRole('navigation', { name: 'Menu principal' })).toBeVisible({ timeout: 20_000 });
      await expect(page.locator(SHELL)).toHaveCount(0);
    }
    await ctx.close();
  });
}

test('camadas: a folha (modal 200) fica acima da tab bar (100) e abaixo de Select/Popover (300)', async ({ page }) => {
  await open(page, '/dashboard/', 'on', { width: 390, height: 844 });
  await expect(page.locator(SHELL)).toBeVisible({ timeout: 20_000 });
  const z = await page.evaluate(() => {
    const css = getComputedStyle(document.documentElement);
    return {
      modal: Number(css.getPropertyValue('--mantine-z-index-modal')),
      popover: Number(css.getPropertyValue('--mantine-z-index-popover')),
    };
  });
  expect(z.popover).toBeGreaterThan(z.modal);
  await page.getByRole('button', { name: 'Mais', exact: true }).click();
  await expect(page.getByRole('dialog', { name: 'Mais' })).toBeVisible();
  const zDialog = await page.locator('.mantine-Drawer-inner').evaluate((el) => Number(getComputedStyle(el).zIndex));
  expect(zDialog).toBe(z.modal);
  expect(zDialog).toBeGreaterThan(100);
  // altura pelo conteudo, com o limite de 85dvh
  const box = await page.locator('.mantine-Drawer-content').boundingBox();
  expect(box!.height).toBeLessThanOrEqual(844 * 0.85 + 1);
});

test('hub Evolução propaga o filtro global e o voltar preserva hub=evolucao', async ({ page }) => {
  await open(page, '/dashboard/?hub=evolucao&window=90d', 'on', { width: 390, height: 844 });
  await expect(page.getByRole('heading', { name: 'Evolução', level: 1 })).toBeVisible({ timeout: 20_000 });
  await page.getByRole('link', { name: 'Progressão' }).click();
  await expect(page).toHaveURL(/\/dashboard\/progressao\/\?window=90d$/);
  await page.getByRole('link', { name: 'Voltar para Evolução' }).click();
  await expect(page).toHaveURL(/\/dashboard\/\?hub=evolucao&window=90d$/);
});

test('Mais: tocar num link fecha a folha', async ({ page }) => {
  await open(page, '/nutricao/', 'on', { width: 390, height: 844 });
  await expect(page.locator(SHELL)).toBeVisible({ timeout: 20_000 });
  await page.getByRole('button', { name: 'Mais', exact: true }).click();
  const sheet = page.getByRole('dialog', { name: 'Mais' });
  await sheet.getByRole('link', { name: 'Nutrição' }).click(); // mesma rota
  await expect(sheet).toBeHidden();
});

test('flag desligada em 375: arvore desktop, sem shell', async ({ page }) => {
  await open(page, '/dashboard/', 'off', { width: 375, height: 800 });
  await expect(page.getByText(READY).first()).toBeVisible({ timeout: 20_000 });
  await expect(page.locator(SHELL)).toHaveCount(0);
});

test('?mobile=1 liga a flag so para QA', async ({ page }) => {
  await open(page, '/dashboard/?mobile=1', null, { width: 375, height: 800 });
  await expect(page.locator(SHELL)).toBeVisible({ timeout: 20_000 });
});

test('tab bar navega; voltar, avancar e refresh preservam a rota', async ({ page }) => {
  await open(page, '/dashboard/', 'on', { width: 390, height: 800 });
  await expect(page.locator(SHELL)).toBeVisible({ timeout: 20_000 });
  const nav = page.getByRole('navigation', { name: 'Navegação principal' });

  await nav.getByRole('link', { name: 'Histórico' }).click();
  await expect(page).toHaveURL(/\/registro-exercicios\/?$/);
  await expect(page.locator(SHELL)).toBeVisible();
  await expect(nav.getByRole('link', { name: 'Histórico' })).toHaveAttribute('aria-current', 'page');

  await nav.getByRole('link', { name: 'Evolução' }).click();
  await expect(page).toHaveURL(/\/dashboard\/\?hub=evolucao$/);
  await expect(page.getByRole('heading', { name: 'Evolução', level: 1 })).toBeVisible();
  await expect(nav.getByRole('link', { name: 'Evolução' })).toHaveAttribute('aria-current', 'page');
  await expect(nav.getByRole('link', { name: 'Início' })).not.toHaveAttribute('aria-current', 'page');

  await page.goBack();
  await expect(page).toHaveURL(/\/registro-exercicios\/?$/);
  await expect(page.locator(SHELL)).toBeVisible();
  await page.goForward();
  await expect(page).toHaveURL(/\/dashboard\/\?hub=evolucao$/);

  await page.reload();
  await expect(page).toHaveURL(/\/dashboard\/\?hub=evolucao$/);
  await expect(page.locator(SHELL)).toBeVisible({ timeout: 20_000 });
  await expect(nav.getByRole('link', { name: 'Evolução' })).toHaveAttribute('aria-current', 'page');
});

test('hub Evolução lista por objetivo; 2º nível tem header com voltar', async ({ page }) => {
  await open(page, '/dashboard/?hub=evolucao', 'on', { width: 390, height: 844 });
  await expect(page.getByRole('heading', { name: 'Evolução', level: 1 })).toBeVisible({ timeout: 20_000 });
  for (const group of ['Treino', 'Corpo e recuperação', 'Cardio e nutrição', 'Análises']) {
    await expect(page.getByRole('heading', { name: group, level: 2 }), group).toBeVisible();
  }
  await expect(page.getByRole('link', { name: /Voltar para/ })).toHaveCount(0);
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: `${ARTIFACTS}/evolucao-390x844.png` });
  await page.getByRole('link', { name: 'Progressão' }).click();
  await expect(page).toHaveURL(/\/dashboard\/progressao\/?$/);
  const back = page.getByRole('link', { name: 'Voltar para Evolução' });
  await expect(back).toBeVisible();
  const box = await back.boundingBox();
  expect(box!.height).toBeGreaterThanOrEqual(44);
  await back.click();
  await expect(page).toHaveURL(/\/dashboard\/\?hub=evolucao$/);
  await expect(page.getByRole('heading', { name: 'Evolução', level: 1 })).toBeVisible();
  await noHorizontalOverflow(page);
});

for (const role of ROLES) {
  test(`"Mais" (${role}): agrupada, sem as seções só-desktop`, async ({ page }) => {
    await open(page, '/dashboard/', 'on', { width: 390, height: 844 }, role);
    await expect(page.locator(SHELL)).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText(READY).first()).toBeVisible({ timeout: 20_000 });
    await page.getByRole('button', { name: 'Mais', exact: true }).click();
    const sheet = page.getByRole('dialog', { name: 'Mais' });
    await expect(sheet).toBeVisible();
    for (const label of ['Planos de treino', 'Exercícios', 'Indicadores Físicos', 'Nutrição', 'Perfil', 'Sair']) {
      await expect(sheet.getByText(label, { exact: true }).first(), label).toBeVisible();
    }
    for (const label of DESKTOP_ONLY_LABELS) {
      await expect(sheet.getByText(label, { exact: true }), label).toHaveCount(0);
    }
    const usuarios = sheet.getByText('Usuários', { exact: true });
    if (role === 'admin') await expect(usuarios).toHaveCount(1);
    else await expect(usuarios).toHaveCount(0);
    if (role === 'user') {
      await page.evaluate(() => document.fonts.ready);
      await page.screenshot({ path: `${ARTIFACTS}/mais-390x844.png` });
      await sheet.getByRole('link', { name: 'Nutrição' }).click();
      await expect(page).toHaveURL(/\/nutricao\/?$/);
      await expect(sheet).toBeHidden();
      await expect(page.getByRole('button', { name: 'Mais', exact: true })).toHaveAttribute('aria-current', 'page');
      // 2º nível de Mais: o voltar reabre a folha
      await page.getByRole('button', { name: 'Voltar para Mais' }).click();
      await expect(page.getByRole('dialog', { name: 'Mais' })).toBeVisible();
    }
  });

  test(`D1 (${role}): rota só-desktop mostra "Disponível no computador" sem chamar a API da rota`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.clock.setFixedTime(FROZEN_NOW);
    await installMockApi(page);
    await asRole(page, role);
    await page.addInitScript(([k, v]) => window.localStorage.setItem(k, v), [FLAG_KEY, 'on']);
    const apiCalls: string[] = [];
    page.on('request', (req) => {
      if (!req.url().startsWith(MOCK_API_ORIGIN)) return;
      const path = new URL(req.url()).pathname;
      if (req.method() === 'OPTIONS' || path === '/auth/me') return;
      apiCalls.push(`${req.method()} ${path}`);
    });
    for (const route of DESKTOP_ONLY_ROUTES) {
      await page.goto(route);
      await expect(page.locator(SHELL), route).toBeVisible({ timeout: 20_000 });
      await expect(
        page.getByRole('heading', { name: 'Disponível no computador', level: 1 }),
        route,
      ).toBeVisible();
      // /tipos-treinamento/ e redirecionada pelo servidor do export (regra do CloudFront
      // Function) antes de o app carregar; as demais mantem a URL.
      const kept = route === '/tipos-treinamento/' ? '/metas-ativacao/' : route;
      expect(new URL(page.url()).pathname, 'a URL é mantida').toBe(kept);
      await noHorizontalOverflow(page);
    }
    await page.waitForLoadState('networkidle');
    expect(apiCalls, 'nenhuma chamada de API das rotas só-desktop').toEqual([]);
    if (role === 'user') {
      await page.goto('/metas-ativacao/');
      await expect(page.getByRole('heading', { name: 'Disponível no computador' })).toBeVisible();
      await page.evaluate(() => document.fonts.ready);
      await page.screenshot({ path: `${ARTIFACTS}/so-desktop-390x844.png` });
      await page.getByRole('link', { name: 'Ir para o Início' }).click();
      await expect(page).toHaveURL(/\/dashboard\/?$/);
      await expect(page.getByRole('heading', { name: 'Início', level: 1 })).toBeVisible({ timeout: 20_000 });
    }
  });
}

for (const width of [1280, 1440]) {
  test(`desktop ${width} (admin, flag ligada): as 6 seções seguem no menu e as rotas abrem`, async ({ page }) => {
    await open(page, '/dashboard/', 'on', { width, height: 900 }, 'admin');
    const menu = page.getByRole('navigation', { name: 'Menu principal' });
    await expect(menu).toBeVisible({ timeout: 20_000 });
    await expect(page.locator(SHELL)).toHaveCount(0);
    for (const label of DESKTOP_ONLY_LABELS) {
      await expect(menu.getByText(label, { exact: true }).first(), label).toBeVisible();
    }
    for (const route of ['/academias/', '/regras-carga/', '/modelos-treino/']) {
      await page.goto(route);
      await expect(menu).toBeVisible({ timeout: 20_000 });
      await expect(page.locator('[data-desktop-only="true"]'), route).toHaveCount(0);
      await expect(page.locator(SHELL)).toHaveCount(0);
    }
  });
}

test('MB-8: teclado aberto esconde a tab bar; fechar a devolve', async ({ page }) => {
  await open(page, '/perfil/', 'on', { width: 390, height: 844 });
  await expect(page.locator(SHELL)).toBeVisible({ timeout: 20_000 });
  const nav = page.getByRole('navigation', { name: 'Navegação principal' });
  await expect(nav).toHaveAttribute('data-hidden', 'false');
  const nome = page.getByLabel('Nome completo');
  await expect(nome).toBeVisible({ timeout: 20_000 });
  await nome.focus();
  await expect(nav).toHaveAttribute('data-hidden', 'true');
  await expect(page.locator(SHELL)).toHaveAttribute('data-keyboard', 'open');
  await nome.blur();
  await expect(nav).toHaveAttribute('data-hidden', 'false');
});

test('rotacao e troca de viewport mudam so a apresentacao', async ({ page }) => {
  await open(page, '/dashboard/', 'on', { width: 390, height: 800 });
  await expect(page.locator(SHELL)).toBeVisible({ timeout: 20_000 });
  await page.setViewportSize({ width: 667, height: 375 });
  await expect(page.locator(SHELL)).toBeVisible();
  await page.setViewportSize({ width: 1024, height: 768 });
  await expect(page.getByRole('navigation', { name: 'Menu principal' })).toBeVisible();
  await expect(page.locator(SHELL)).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 800 });
  await expect(page.locator(SHELL)).toBeVisible();
  expect(new URL(page.url()).pathname).toBe('/dashboard/');
});

test('A2: campos e botoes das paginas P1 tem >= 44px e fonte >= 16px no shell', async ({ page }) => {
  await open(page, '/perfil/', 'on', { width: 375, height: 800 });
  await expect(page.locator(SHELL)).toBeVisible({ timeout: 20_000 });
  const nome = page.getByLabel('Nome completo');
  await expect(nome).toBeVisible({ timeout: 20_000 });
  const box = await nome.boundingBox();
  expect(box!.height).toBeGreaterThanOrEqual(44);
  const fontSize = await nome.evaluate((el) => parseFloat(getComputedStyle(el).fontSize));
  expect(fontSize).toBeGreaterThanOrEqual(16);
  await noHorizontalOverflow(page);
  await page.screenshot({ path: 'e2e/mobile-layout/__screens__/perfil-375.png' });
});

test('A2: historico, biblioteca e treinos sem overflow de pagina em 320', async ({ page }) => {
  await open(page, '/dashboard/treinos/', 'on', { width: 320, height: 800 });
  await expect(page.locator(SHELL)).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText(/costas e biceps/i).first()).toBeVisible({ timeout: 20_000 });
  await noHorizontalOverflow(page);
  for (const path of ['/registro-exercicios/', '/exercicios/']) {
    await page.goto(path);
    await expect(page.locator(SHELL)).toBeVisible({ timeout: 20_000 });
    await page.waitForLoadState('networkidle');
    await noHorizontalOverflow(page);
  }
});

test('A2b: Inicio mobile tem CTA de iniciar treino no alcance do polegar e usa os dados do overview', async ({ page }) => {
  await open(page, '/dashboard/', 'on', { width: 375, height: 800 });
  await expect(page.locator(SHELL)).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole('heading', { name: 'Início', level: 1 })).toBeVisible({ timeout: 20_000 });
  const cta = page.getByRole('link', { name: /iniciar treino/i });
  await expect(cta).toBeVisible();
  const box = await cta.boundingBox();
  expect(box!.height).toBeGreaterThanOrEqual(44);
  // metade inferior da tela (zona do polegar) e acima da tab bar
  expect(box!.y).toBeGreaterThan(400);
  const navBox = await page.getByRole('navigation', { name: 'Navegação principal' }).boundingBox();
  expect(box!.y + box!.height).toBeLessThanOrEqual(navBox!.y);
  await expect(page.getByText(/voce concluiu 10 de 12/i).first()).toBeVisible();
  await expect(page.getByText('Recuperação', { exact: true }).first()).toBeVisible();
  await expect(page.getByText('Próxima ação', { exact: true }).first()).toBeVisible();
  await noHorizontalOverflow(page);
  await page.screenshot({ path: 'e2e/mobile-layout/__screens__/inicio-375.png' });
  await cta.click();
  await expect(page).toHaveURL(/\/modo-treino\/?$/);
});

test('A2b: visao geral completa fica recolhida e abre sob demanda', async ({ page }) => {
  await open(page, '/dashboard/', 'on', { width: 375, height: 800 });
  await expect(page.getByRole('heading', { name: 'Início', level: 1 })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole('heading', { name: 'Visão geral', level: 1 })).toBeHidden();
  await page.getByRole('button', { name: /ver visão geral completa/i }).click();
  await expect(page.getByRole('heading', { name: 'Visão geral', level: 1 })).toBeVisible();
  await noHorizontalOverflow(page);
});
