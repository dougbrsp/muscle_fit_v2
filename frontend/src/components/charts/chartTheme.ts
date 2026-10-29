// Tokens compartilhados para gráficos recharts, alinhados ao tema escuro da
// marca (ver src/theme.ts). Mantém consistência visual entre as telas
// analíticas (hub /dashboard/*, volume-grupamentos, indicadores-fisicos...)
// sem duplicar estilo em cada arquivo.
import type { CSSProperties } from 'react';
import { BRAND } from '@/theme';

export const CHART_GRID_STROKE = BRAND.border;
export const CHART_AXIS_TICK = { fill: BRAND.textMuted, fontSize: 12 };

export const CHART_TOOLTIP_CONTENT_STYLE: CSSProperties = {
  background: BRAND.bgSurface,
  border: `1px solid ${BRAND.border}`,
  borderRadius: 12,
  color: BRAND.textPrimary,
  fontSize: 13,
};

export const CHART_TOOLTIP_LABEL_STYLE: CSSProperties = {
  color: BRAND.textPrimary,
  fontWeight: 600,
};

export const CHART_TOOLTIP_ITEM_STYLE: CSSProperties = {
  color: BRAND.textMuted,
};

// Ordem categórica fixa (nunca ciclada): laranja de marca primeiro, depois
// vermelho de marca, depois um terciário neutro para séries de comparação
// ("anterior"/"previous"), evitando cores genéricas do recharts.
export const CHART_COLORS = {
  primary: BRAND.orange,
  secondary: BRAND.red,
  compare: '#8B93A6', // cinza-azulado neutro, para série "anterior"/comparação
  positive: '#2f9e44',
  warning: BRAND.orange,
  negative: BRAND.red,
} as const;

export const CHART_LEGEND_STYLE: CSSProperties = {
  color: BRAND.textMuted,
  fontSize: 12,
};

// Linhas de dados passadas aos módulos de gráfico carregados sob demanda
// (COD-14). Só tipo: importar isto não traz o recharts para o bundle.
export type ChartRows = Array<Record<string, unknown>>;
