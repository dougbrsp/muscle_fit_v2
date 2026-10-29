'use client';

// Gráficos (recharts) da progressão de um exercício, em módulo próprio para
// carga sob demanda (`next/dynamic`, COD-14).
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
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
import type { buildRpeSeries, buildTrendSeries } from '@/lib/exercise-progress';

export function TrendChart({ trend }: { trend: ReturnType<typeof buildTrendSeries> }) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <LineChart data={trend.points} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
        <CartesianGrid stroke={CHART_GRID_STROKE} strokeDasharray="3 3" />
        <XAxis dataKey="label" tick={CHART_AXIS_TICK} stroke={CHART_GRID_STROKE} />
        {/* Eixo Y ÚNICO: uma série só, uma grandeza só. Nunca dual-axis
            (regra documentada em DashboardProgressaoClient.tsx). */}
        <YAxis
          tick={CHART_AXIS_TICK}
          stroke={CHART_GRID_STROKE}
          unit={trend.unit ? ` ${trend.unit}` : undefined}
          width={70}
        />
        <RechartsTooltip
          contentStyle={CHART_TOOLTIP_CONTENT_STYLE}
          labelStyle={CHART_TOOLTIP_LABEL_STYLE}
          itemStyle={CHART_TOOLTIP_ITEM_STYLE}
        />
        <Line
          type="monotone"
          dataKey="value"
          name={trend.label}
          stroke={CHART_COLORS.primary}
          strokeWidth={2}
          dot
          connectNulls
        />
      </LineChart>
    </ResponsiveContainer>
  );
}

export function RpeChart({ rpe }: { rpe: ReturnType<typeof buildRpeSeries> }) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <LineChart data={rpe.points} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
        <CartesianGrid stroke={CHART_GRID_STROKE} strokeDasharray="3 3" />
        <XAxis dataKey="label" tick={CHART_AXIS_TICK} stroke={CHART_GRID_STROKE} />
        {/* Escala fixa 0–10: é a escala do RPE. Autoescalar faria uma
            variação de 7,8 para 8,0 ocupar o gráfico inteiro. */}
        <YAxis
          tick={CHART_AXIS_TICK}
          stroke={CHART_GRID_STROKE}
          domain={[0, 10]}
          ticks={[0, 2, 4, 6, 8, 10]}
          width={40}
        />
        <RechartsTooltip
          contentStyle={CHART_TOOLTIP_CONTENT_STYLE}
          labelStyle={CHART_TOOLTIP_LABEL_STYLE}
          itemStyle={CHART_TOOLTIP_ITEM_STYLE}
        />
        <Line
          type="monotone"
          dataKey="value"
          name="RPE médio"
          stroke={CHART_COLORS.primary}
          strokeWidth={2}
          dot
          connectNulls
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
