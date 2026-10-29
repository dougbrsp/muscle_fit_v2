'use client';

// Gráfico (recharts) da linha ÚNICA de e1RM (M97 F4), em módulo próprio para
// carga sob demanda (`next/dynamic`, COD-14). Usado pelo drill-down do
// exercício e pelo painel de força.
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
import type { UnifiedE1rmChart as UnifiedE1rmChartData } from '@/lib/exercise-progress';

/**
 * Linha de e1RM de TODOS os objetivos (o e1RM é único), pontos de força em
 * destaque e as sessões de deload como linhas verticais tracejadas, fora da
 * linha. Tudo pronto em `buildUnifiedE1rmChart`; aqui só se desenha.
 */
export function UnifiedE1rmChart({ chart, compact = false }: { chart: UnifiedE1rmChartData; compact?: boolean }) {
  const labelByKey = new Map(chart.points.map((point) => [point.xKey, point.label]));
  return (
    <ResponsiveContainer width="100%" height="100%">
      <ComposedChart data={chart.points} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
        <CartesianGrid stroke={CHART_GRID_STROKE} strokeDasharray="3 3" />
        <XAxis
          dataKey="xKey"
          tick={compact ? false : CHART_AXIS_TICK}
          stroke={CHART_GRID_STROKE}
          tickFormatter={(key: string) => labelByKey.get(String(key)) ?? ''}
        />
        <YAxis
          tick={CHART_AXIS_TICK}
          stroke={CHART_GRID_STROKE}
          unit=" kg"
          width={compact ? 56 : 70}
          domain={['auto', 'auto']}
        />
        <RechartsTooltip
          contentStyle={CHART_TOOLTIP_CONTENT_STYLE}
          labelStyle={CHART_TOOLTIP_LABEL_STYLE}
          itemStyle={CHART_TOOLTIP_ITEM_STYLE}
          labelFormatter={(key) => labelByKey.get(String(key)) ?? ''}
        />
        {chart.points
          .filter((point) => point.isDeload)
          .map((point) => (
            <ReferenceLine
              key={`deload-${point.xKey}`}
              x={point.xKey}
              stroke={CHART_COLORS.compare}
              strokeDasharray="4 4"
              label={
                compact
                  ? undefined
                  : { value: point.deloadLabel ?? 'Deload', position: 'insideTop', fontSize: 10, fill: CHART_COLORS.compare }
              }
            />
          ))}
        <Line
          type="monotone"
          dataKey="value"
          name="1RM estimado"
          stroke={CHART_COLORS.primary}
          strokeWidth={2}
          dot={!compact}
          connectNulls
        />
        {chart.strengthPoints > 0 && (
          <Scatter dataKey="strengthValue" name="1RM estimado (força)" fill={CHART_COLORS.secondary} />
        )}
      </ComposedChart>
    </ResponsiveContainer>
  );
}
