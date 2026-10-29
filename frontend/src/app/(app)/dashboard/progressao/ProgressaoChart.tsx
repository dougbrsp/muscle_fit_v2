'use client';

// Gráfico (recharts) de 1RM estimado e carga de topo, em módulo próprio para
// carga sob demanda (`next/dynamic`, COD-14).
import {
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from 'recharts';
import {
  CHART_AXIS_TICK,
  CHART_COLORS,
  CHART_GRID_STROKE,
  CHART_TOOLTIP_CONTENT_STYLE,
  CHART_TOOLTIP_ITEM_STYLE,
  CHART_TOOLTIP_LABEL_STYLE,
} from '@/components/charts/chartTheme';
import type { ProgressionChartPoint } from '@/lib/progression-chart';

/**
 * M97 F4: e1RM ÚNICO (todos os objetivos) com os pontos de FORÇA destacados
 * (`e1rmForca`) e as sessões de deload como linhas verticais tracejadas, fora
 * da linha de e1RM. Tudo vem pronto de `buildProgressionChart`.
 */
export function ProgressaoChart({ chartData }: { chartData: ProgressionChartPoint[] }) {
  const labelByKey = new Map(chartData.map((point, index) => [point.xKey ?? `${index}`, point.label]));
  const hasStrength = chartData.some((point) => point.e1rmForca != null);
  return (
    <ResponsiveContainer width="100%" height="100%">
      <ComposedChart data={chartData} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
        <CartesianGrid stroke={CHART_GRID_STROKE} strokeDasharray="3 3" />
        <XAxis
          dataKey="xKey"
          tick={CHART_AXIS_TICK}
          stroke={CHART_GRID_STROKE}
          tickFormatter={(key: string) => labelByKey.get(String(key)) ?? ''}
        />
        <YAxis tick={CHART_AXIS_TICK} stroke={CHART_GRID_STROKE} unit=" kg" width={70} />
        <RechartsTooltip
          contentStyle={CHART_TOOLTIP_CONTENT_STYLE}
          labelStyle={CHART_TOOLTIP_LABEL_STYLE}
          itemStyle={CHART_TOOLTIP_ITEM_STYLE}
          labelFormatter={(key) => labelByKey.get(String(key)) ?? ''}
        />
        {chartData
          .filter((point) => point.deload)
          .map((point) => (
            <ReferenceLine
              key={`deload-${point.xKey}`}
              x={point.xKey}
              stroke={CHART_COLORS.compare}
              strokeDasharray="4 4"
              label={{ value: point.deloadLabel ?? 'Deload', position: 'insideTop', fontSize: 10, fill: CHART_COLORS.compare }}
            />
          ))}
        <Line
          type="monotone"
          dataKey="e1rm"
          name="1RM estimado"
          stroke={CHART_COLORS.primary}
          strokeWidth={2}
          dot={false}
          connectNulls
        />
        <Line
          type="monotone"
          dataKey="carga"
          name="Carga de topo"
          stroke={CHART_COLORS.compare}
          strokeWidth={2}
          dot={false}
          connectNulls
        />
        {hasStrength && (
          <Scatter dataKey="e1rmForca" name="1RM estimado (força)" fill={CHART_COLORS.secondary} />
        )}
      </ComposedChart>
    </ResponsiveContainer>
  );
}
