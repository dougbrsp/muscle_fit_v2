import { BRAND, WORKOUT_MODE_TOKENS } from '@/theme';

// Tokens do shell mobile (plano mobile, 3.3; mobile v2). Derivados de `BRAND` e
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
  /** Texto secundario (rotulos, subtitulos): ~6:1 sobre `surface` (AA). */
  textSecondary: 'rgba(245, 245, 245, 0.64)',
  /** Apenas decoracao (chevron, separador de texto): nao carrega informacao. */
  textTertiary: 'rgba(245, 245, 245, 0.38)',
  /** Divisor fino entre linhas de uma lista agrupada. */
  separator: 'rgba(255, 255, 255, 0.08)',
  danger: WORKOUT_MODE_TOKENS.danger,
  warning: WORKOUT_MODE_TOKENS.warning,
  success: WORKOUT_MODE_TOKENS.success,
  touchTarget: 44,
  headerHeight: 52,
  bottomNavHeight: 56,
  /** Alturas em paisagem de celular (altura de tela baixa). */
  headerHeightLandscape: 44,
  bottomNavHeightLandscape: 48,
  contentPadding: 16,
  /** Raio dos grupos/cartoes (superficie agrupada, estilo iOS). */
  radiusGroup: 14,
  radiusSheet: 20,
  /** Gap entre grupos de uma tela. */
  groupGap: 24,
  /** Altura compacta padrao de grafico no celular. */
  chartHeight: 180,
  chartHeightMax: 240,
} as const;

/**
 * Escala tipografica (px / line-height / peso), inspirada nos estilos de texto
 * do iOS e ajustada para densidade confortavel. Fonte de sistema. Use estes
 * tokens (ou as classes de `ui/ui.module.css`), nunca tamanhos soltos.
 * Corpo de campo de formulario fica em 16 px (sem zoom do iOS ao focar).
 */
export const MOBILE_TYPE = {
  largeTitle: { size: 28, line: 34, weight: 700 },
  title: { size: 22, line: 28, weight: 700 },
  headline: { size: 17, line: 22, weight: 600 },
  body: { size: 16, line: 22, weight: 400 },
  callout: { size: 15, line: 20, weight: 400 },
  subhead: { size: 14, line: 19, weight: 400 },
  footnote: { size: 13, line: 18, weight: 400 },
  caption: { size: 11, line: 14, weight: 500 },
  /** Numero em destaque de StatTile/DataCard. */
  metric: { size: 26, line: 30, weight: 700 },
} as const;

export type MobileTypeStyle = keyof typeof MOBILE_TYPE;

export const MOBILE_FONT_FAMILY =
  "-apple-system, BlinkMacSystemFont, 'SF Pro Text', 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif";

/** CSS inline de um estilo tipografico (para componentes sem classe). */
export function typeStyle(name: MobileTypeStyle): {
  fontSize: number;
  lineHeight: string;
  fontWeight: number;
} {
  const t = MOBILE_TYPE[name];
  return { fontSize: t.size, lineHeight: `${t.line}px`, fontWeight: t.weight };
}
