import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

// Testes unitarios de libs puras (src/**/*.test.ts). Sem DOM: environment
// "node" - componentes sao verificados por `renderToStaticMarkup` (SSR),
// sem jsdom nem testing-library. Nao participa do `next build` (output export so gera rotas de
// src/app).
export default defineConfig({
  // O tsconfig do Next usa `jsx: preserve` (o Next transforma). Aqui quem
  // transforma e o esbuild do Vite, que nao aceita `preserve` - sem isto o
  // teste de render do painel de metodologia (que importa um .tsx) quebra.
  esbuild: { jsx: 'automatic' },
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  test: {
    environment: 'node',
    // scripts/**: teste da CloudFront Function real usada no preview/smoke do export.
    include: ['src/**/*.test.ts', 'scripts/**/*.test.mjs'],
  },
});
