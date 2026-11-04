import type { ComponentType } from 'react';

/**
 * Telas mobile da frente B (Evolução e dashboards). Chave = rota sem barra
 * final (segmentos dinâmicos como `[id]`); valor = `lazyScreen(...)`. Compõe-se
 * em `registry.ts`. Dono: frente B (`src/mobile/screens/evolucao/**`).
 */
export const SCREENS_B: Readonly<Record<string, ComponentType>> = {};
