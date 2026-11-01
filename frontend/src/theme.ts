import { createTheme, type MantineColorsTuple } from '@mantine/core';

// Tokens de marca — ver docs/brand/brand-guide.md.
// Fundo escuro (~#0A0A0A), acento em gradiente laranja -> vermelho-alaranjado
// (#FF9500 -> #E8451C), branco para contraste. Tema escuro é o padrão do app
// (decisão confirmada em 2026-09-04), não apenas uma cor de acento sobre claro.

export const BRAND = {
  bg: '#0A0A0A',
  bgElevated: '#141414',
  bgSurface: '#1C1C1C',
  border: '#2A2A2A',
  orange: '#FF9500',
  red: '#E8451C',
  textPrimary: '#F5F5F5',
  textMuted: '#E0E0E0',
  gradient: { from: '#FF9500', to: '#E8451C', deg: 135 },
} as const;

// Tokens de UI do novo visual do Modo Treino (PR1/3). NÃO são cores da marca:
// ficam fora de `BRAND` porque `BRAND` é a paleta lida pelo prompt do card
// ilustrado (`api/app/exercise_card_prompt.py`, `BRAND_COLORS`, conferida por
// `test_cores_sao_os_tokens_reais_do_theme_ts`), e cores semânticas de estado
// não entram em prompt de imagem. Superfície ELEVADA (controles e cards em
// evidência, acima de `BRAND.bgSurface`), laranja secundário (destaques,
// estado pré-fatigado) e os semânticos de estado.
// Cor nunca é o único sinal: todo estado leva rótulo e ícone.
export const WORKOUT_MODE_TOKENS = {
  bgRaised: '#202124',
  orangeSoft: '#FFAB32',
  success: '#32D483',
  danger: '#F34B4B',
  warning: '#FFCA28',
} as const;

// Tokens do shell mobile (plano mobile, 3.3). Derivados de `BRAND` e
// `WORKOUT_MODE_TOKENS`; só dimensões de layout e aliases de cor. NÃO são
// regra de domínio. `touchTarget` = alvo mínimo de toque (WCAG 2.5.5 / HIG).
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

// Escala de laranja/vermelho para uso como `color="brand"` nos componentes Mantine.
const brandTuple: MantineColorsTuple = [
  '#fff2e0',
  '#ffe0b8',
  '#ffc989',
  '#ffb156',
  '#ff9d2e',
  '#ff9500',
  '#f58600',
  '#da7300',
  '#c26300',
  '#a85200',
];

const redTuple: MantineColorsTuple = [
  '#fdeae5',
  '#f9c9ba',
  '#f4a68c',
  '#ef825d',
  '#eb6538',
  '#e8541f',
  '#e8451c',
  '#c9370f',
  '#b32e0c',
  '#9c2405',
];

export const theme = createTheme({
  primaryColor: 'brand',
  colors: {
    brand: brandTuple,
    brandRed: redTuple,
  },
  fontFamily:
    "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
  headings: {
    fontFamily:
      "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
    fontWeight: '700',
  },
  defaultRadius: 'md',
  black: BRAND.bg,
  white: BRAND.textPrimary,
  components: {
    Button: {
      defaultProps: {
        radius: 'md',
      },
    },
  },
});
