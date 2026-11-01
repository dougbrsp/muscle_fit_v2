import { defineConfig, devices } from '@playwright/test';

/**
 * Resiliencia do Modo Treino (Frente B do plano mobile): sessao, fila offline,
 * cronometros e troca de conta, com a API 100% mockada
 * (`e2e/workout-resilience/mock-workout-api.ts`). Roda contra o export estatico
 * (`npm run e2e` ja o gera em `out/`) ou contra um servidor existente via
 * `E2E_BASE_URL` (ex.: `next dev`).
 *
 *   npx playwright test -c playwright.resilience.config.ts
 */
const PORT = 4174;
const externalBase = process.env.E2E_BASE_URL;

export default defineConfig({
  testDir: './e2e/workout-resilience',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 90_000,
  reporter: [['list']],
  use: {
    baseURL: externalBase ?? `http://127.0.0.1:${PORT}`,
    locale: 'pt-BR',
    timezoneId: 'America/Sao_Paulo',
    colorScheme: 'dark',
    reducedMotion: 'reduce',
    serviceWorkers: 'block',
    viewport: { width: 390, height: 844 },
  },
  projects: [{ name: 'chromium-390', use: { ...devices['Pixel 7'], viewport: { width: 390, height: 844 } } }],
  webServer: externalBase
    ? undefined
    : {
        command: `node scripts/serve-export.mjs --port ${PORT} --dir out`,
        url: `http://127.0.0.1:${PORT}/`,
        reuseExistingServer: true,
        timeout: 30_000,
      },
});
