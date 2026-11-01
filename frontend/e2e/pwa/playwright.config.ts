import { defineConfig, devices } from '@playwright/test';

/**
 * E2E do PWA (frente C): manifest, convite de instalacao e service worker.
 * Config separada da do baseline desktop (que bloqueia SW). Roda contra o
 * export estatico (`node e2e/build.mjs` -> `out/`), API mockada, Chromium.
 *
 *   npm run e2e:build   # nao existe script; use: node e2e/build.mjs
 *   npx playwright test -c e2e/pwa/playwright.config.ts
 */
const PORT = 4174;

export default defineConfig({
  testDir: '.',
  testMatch: '**/*.spec.ts',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
  reporter: [['list']],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    locale: 'pt-BR',
    timezoneId: 'America/Sao_Paulo',
    colorScheme: 'dark',
    serviceWorkers: 'allow',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: `node scripts/serve-export.mjs --port ${PORT} --dir out`,
    cwd: '../..',
    url: `http://127.0.0.1:${PORT}/`,
    reuseExistingServer: true,
    timeout: 30_000,
  },
});
