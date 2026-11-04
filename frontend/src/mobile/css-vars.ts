import type { CSSProperties } from 'react';
import { MOBILE_FONT_FAMILY, MOBILE_TOKENS } from './tokens';

/**
 * Variaveis CSS `--m-*` do iPhone, a partir de MOBILE_TOKENS. O shell as aplica
 * no `.root`; folhas (Drawer do Mantine) renderizam num portal FORA do `.root`
 * e por isso reaplicam as mesmas variaveis.
 */
export const MOBILE_CSS_VARS = {
  '--m-bg': MOBILE_TOKENS.bg,
  '--m-surface': MOBILE_TOKENS.surface,
  '--m-raised': MOBILE_TOKENS.surfaceRaised,
  '--m-border': MOBILE_TOKENS.border,
  '--m-sep': MOBILE_TOKENS.separator,
  '--m-accent': MOBILE_TOKENS.accent,
  '--m-text': MOBILE_TOKENS.text,
  '--m-text-muted': MOBILE_TOKENS.textMuted,
  '--m-text-2': MOBILE_TOKENS.textSecondary,
  '--m-text-3': MOBILE_TOKENS.textTertiary,
  '--m-danger': MOBILE_TOKENS.danger,
  '--m-touch': `${MOBILE_TOKENS.touchTarget}px`,
  '--m-header-base': `${MOBILE_TOKENS.headerHeight}px`,
  '--m-header-landscape': `${MOBILE_TOKENS.headerHeightLandscape}px`,
  '--m-bottom-nav-base': `${MOBILE_TOKENS.bottomNavHeight}px`,
  '--m-bottom-nav-landscape': `${MOBILE_TOKENS.bottomNavHeightLandscape}px`,
  '--m-pad': `${MOBILE_TOKENS.contentPadding}px`,
  '--m-radius': `${MOBILE_TOKENS.radiusGroup}px`,
  '--m-group-gap': `${MOBILE_TOKENS.groupGap}px`,
  '--m-font': MOBILE_FONT_FAMILY,
} as CSSProperties;
