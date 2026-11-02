import { BRAND, WORKOUT_MODE_TOKENS } from '@/theme';

// Tokens do shell mobile (plano mobile, 3.3). Derivados de `BRAND` e
// `WORKOUT_MODE_TOKENS`; so dimensoes de layout e aliases de cor. NAO sao
// regra de dominio. `touchTarget` = alvo minimo de toque (WCAG 2.5.5 / HIG).
// Ficam em src/mobile (e nao em theme.ts) para nao crescer o bundle shared.
export const MOBILE_TOKENS = {
  bg: BRAND.bg,
  surface: BRAND.bgElevated,
  surfaceRaised: BRAND.bgSurface,
  border: BRAND.border,
  accent: BRAND.orange,
  accentSoft: WORKOUT_MODE_TOKENS.orangeSoft,
  text: BRAND.textPrimary,
  textMuted: BRAND.textMuted,
  danger: WORKOUT_MODE_TOKENS.danger,
  warning: WORKOUT_MODE_TOKENS.warning,
  touchTarget: 44,
  headerHeight: 56,
  bottomNavHeight: 60,
  contentPadding: 16,
} as const;
