import { defineConfig, devices } from '@playwright/test';

/**
 * Baseline de regressao visual do DESKTOP (PR1 do plano mobile): prova que as
 * mudancas mobile nao alteram o desktop. Roda contra o export estatico
 * (`e2e/build.mjs` -> `out/`) servido localmente por `scripts/serve-export.mjs`,
 * com a API 100% mockada (`e2e/desktop-baseline/mock-api.ts`): sem backend,
 * sem rede externa, relogio congelado.
 *
 * Uso local (NAO roda no CI por enquanto):
 *   npm run e2e                  # build + testes (Chromium: prova autoritativa)
 *   npm run e2e:webkit           # WebKit, OPCIONAL: oscila no Windows (viewport,
 *                                # tolerancia 0.2%); falha de WebKit nao bloqueia o PR
 *   npm run e2e:test             # so os testes (reusa out/ existente)
 *   npm run e2e:update           # regenera os snapshots de referencia
 *
 * Os goldens foram gerados no Windows; o render de fonte difere por SO, por
 * isso o caminho do snapshot nao leva sufixo de plataforma e a comparacao so
 * e valida no mesmo SO. Ao levar para o CI (Linux), regenerar la.
 */
// Porta por env para agentes/execucoes paralelos nao colidirem (default 4179).
const PORT = Number(process.env.E2E_PORT ?? 4179);

export default defineConfig({
  testDir: './e2e/desktop-baseline',
  snapshotPathTemplate: '{testDir}/__screenshots__/{projectName}/{arg}{ext}',
  fullyParallel: true,
  workers: 1, // WebKit oscila em screenshots fullPage sob carga paralela
  retries: 0,
  timeout: 60_000,
  reporter: [['list']],
  expect: {
    toHaveScreenshot: { animations: 'disabled', caret: 'hide', maxDiffPixels: 0 },
  },
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    locale: 'pt-BR',
    timezoneId: 'America/Sao_Paulo',
    colorScheme: 'dark',
    reducedMotion: 'reduce',
    serviceWorkers: 'block',
  },
  projects: [
    {
      name: 'chromium-1280',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 800 } },
    },
    {
      name: 'chromium-1440',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } },
    },
    {
      name: 'webkit-1280',
      use: { ...devices['Desktop Safari'], viewport: { width: 1280, height: 800 } },
    },
    {
      name: 'webkit-1440',
      use: { ...devices['Desktop Safari'], viewport: { width: 1440, height: 900 } },
    },
  ],
  webServer: {
    command: `node scripts/serve-export.mjs --port ${PORT} --dir out`,
    url: `http://127.0.0.1:${PORT}/`,
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
