'use client';

import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip as ReTooltip, XAxis, YAxis } from 'recharts';
import {
  CHART_AXIS_TICK,
  CHART_COLORS,
  CHART_GRID_STROKE,
  CHART_LEGEND_STYLE,
  CHART_TOOLTIP_CONTENT_STYLE,
  CHART_TOOLTIP_ITEM_STYLE,
  CHART_TOOLTIP_LABEL_STYLE,
} from '@/components/charts/chartTheme';
import { BRAND } from '@/theme';

// Gráfico da comparação rascunho × vigente, carregado sob demanda por PlanComparison (COD-14).
// Só desenha os valores prontos da API; `format` vem do pai.

const HATCH_ID = 'hachura-rascunho';

export type ComparisonChartRow = { label: string; vigente: number | null; rascunho: number | null };

export default function PlanComparisonChart({
  rows,
  format,
}: {
  rows: ComparisonChartRow[];
  format: (value: number | null) => string;
}) {
  return (
    <ResponsiveContainer width="100%" height={220}>
      <BarChart data={rows} margin={{ top: 8, right: 8, left: 0, bottom: 4 }} barGap={2}>
        <defs>
          <pattern id={HATCH_ID} width={6} height={6} patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect width={6} height={6} fill={BRAND.bgSurface} />
            <line x1={0} y1={0} x2={0} y2={6} stroke={CHART_COLORS.primary} strokeWidth={3} />
          </pattern>
        </defs>
        <CartesianGrid stroke={CHART_GRID_STROKE} strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="label" tick={CHART_AXIS_TICK} interval={0} />
        <YAxis tick={CHART_AXIS_TICK} width={44} />
        <ReTooltip
          contentStyle={CHART_TOOLTIP_CONTENT_STYLE}
          labelStyle={CHART_TOOLTIP_LABEL_STYLE}
          itemStyle={CHART_TOOLTIP_ITEM_STYLE}
          formatter={(value, name) => [format(value == null ? null : Number(value)), String(name)]}
        />
        <Legend wrapperStyle={CHART_LEGEND_STYLE} />
        <Bar dataKey="vigente" name="Vigente" fill={CHART_COLORS.compare} radius={[4, 4, 0, 0]} maxBarSize={22} isAnimationActive={false} />
        <Bar
          dataKey="rascunho"
          name="Rascunho (hachurado)"
          fill={`url(#${HATCH_ID})`}
          stroke={CHART_COLORS.primary}
          strokeWidth={1.5}
          radius={[4, 4, 0, 0]}
          maxBarSize={22}
          isAnimationActive={false}
        />
      </BarChart>
    </ResponsiveContainer>
  );
}
