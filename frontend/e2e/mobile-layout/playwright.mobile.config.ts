import { defineConfig, devices } from '@playwright/test';

/**
 * E2E do layout mobile (Frente A). Mesmo export estatico do baseline desktop
 * (`node e2e/build.mjs`), API mockada. A flag e ligada por override de
 * localStorage (`musclefit.flag.mobile-ui`), entao nao precisa de build proprio.
 * Porta propria (4192) e sem reaproveitar servidor: nunca testa o `out/` de outro worktree.
 *
 *   npx playwright test -c e2e/mobile-layout/playwright.mobile.config.ts
 */
const PORT = 4192;

export default defineConfig({
  testDir: '.',
  testMatch: /.*\.spec\.ts/,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
  reporter: [['list']],
  outputDir: '../../test-results/mobile-layout',
  use: {
    ...devices['Desktop Chrome'],
    baseURL: `http://127.0.0.1:${PORT}`,
    locale: 'pt-BR',
    timezoneId: 'America/Sao_Paulo',
    colorScheme: 'dark',
    serviceWorkers: 'block',
  },
  webServer: {
    command: `node scripts/serve-export.mjs --port ${PORT} --dir out`,
    cwd: '../..',
    url: `http://127.0.0.1:${PORT}/`,
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
