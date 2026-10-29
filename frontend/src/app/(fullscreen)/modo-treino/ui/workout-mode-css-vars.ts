import type { CSSProperties } from 'react';
import { BRAND, WORKOUT_MODE_TOKENS } from '@/theme';

/**
 * Variáveis CSS do novo visual do Modo Treino, derivadas dos tokens `BRAND`
 * e `WORKOUT_MODE_TOKENS` (`theme.ts`), a fonte única das cores. Aplicadas no elemento raiz da tela
 * (`PlanExecutionScreen`) e lidas por `workout-mode.module.css`: o CSS não
 * repete hexadecimal e nada vaza para fora do Modo Treino.
 */
export const WORKOUT_MODE_CSS_VARS = {
  '--wm-bg': BRAND.bg,
  '--wm-surface': BRAND.bgElevated,
  '--wm-raised': WORKOUT_MODE_TOKENS.bgRaised,
  '--wm-border': BRAND.border,
  '--wm-orange': BRAND.orange,
  '--wm-orange-soft': WORKOUT_MODE_TOKENS.orangeSoft,
  '--wm-orange-border': `${BRAND.orange}99`,
  '--wm-orange-glow': `${BRAND.orange}66`,
  '--wm-orange-glow-inner': `${BRAND.orange}26`,
  '--wm-text': BRAND.textPrimary,
  '--wm-text-muted': BRAND.textMuted,
  '--wm-success': WORKOUT_MODE_TOKENS.success,
  '--wm-danger': WORKOUT_MODE_TOKENS.danger,
  '--wm-warning': WORKOUT_MODE_TOKENS.warning,
} as CSSProperties;
