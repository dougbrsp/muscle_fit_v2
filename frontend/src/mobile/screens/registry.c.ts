import type { ComponentType } from 'react';

/**
 * Telas mobile da frente C (Treino, corpo, nutrição e conta). Chave = rota sem
 * barra final (segmentos dinâmicos como `[id]`); valor = `lazyScreen(...)`.
 * Compõe-se em `registry.ts`. Dono: frente C (`src/mobile/screens/telas/**`).
 */
export const SCREENS_C: Readonly<Record<string, ComponentType>> = {};
