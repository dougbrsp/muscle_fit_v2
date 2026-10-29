'use client';

// Gráficos (recharts) de /exercicios, em módulo próprio para carga sob
// demanda (`next/dynamic`, COD-14).
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip as ReTooltip, XAxis, YAxis } from 'recharts';
import {
  CHART_AXIS_TICK,
  CHART_COLORS,
  CHART_GRID_STROKE,
  CHART_LEGEND_STYLE,
  CHART_TOOLTIP_CONTENT_STYLE,
  CHART_TOOLTIP_ITEM_STYLE,
  CHART_TOOLTIP_LABEL_STYLE,
  type ChartRows,
} from '@/components/charts/chartTheme';
import { formatFixed, formatKgFixed } from '@/lib/format';

export function WeightChart({ weightChartData }: { weightChartData: ChartRows }) {
  return (
    <ResponsiveContainer width="100%" height={280}>
      <LineChart data={weightChartData} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
        <CartesianGrid stroke={CHART_GRID_STROKE} strokeDasharray="4 4" vertical={false} />
        <XAxis dataKey="data" tick={CHART_AXIS_TICK} tickMargin={8} />
        <YAxis tick={CHART_AXIS_TICK} width={56} />
        <ReTooltip
          contentStyle={CHART_TOOLTIP_CONTENT_STYLE}
          labelStyle={CHART_TOOLTIP_LABEL_STYLE}
          itemStyle={CHART_TOOLTIP_ITEM_STYLE}
          formatter={(value) => formatKgFixed(Number(value), 1)}
        />
        <Legend wrapperStyle={CHART_LEGEND_STYLE} />
        <Line type="monotone" dataKey="melhorCarga" name="Melhor carga" stroke={CHART_COLORS.primary} strokeWidth={2} dot={false} activeDot={{ r: 5 }} connectNulls />
        <Line type="monotone" dataKey="volume" name="Volume" stroke={CHART_COLORS.compare} strokeWidth={2} dot={false} activeDot={{ r: 5 }} connectNulls />
      </LineChart>
    </ResponsiveContainer>
  );
}

export function RpeChart({ rpeChartData }: { rpeChartData: ChartRows }) {
  return (
    <ResponsiveContainer width="100%" height={280}>
      <LineChart data={rpeChartData} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
        <CartesianGrid stroke={CHART_GRID_STROKE} strokeDasharray="4 4" vertical={false} />
        <XAxis dataKey="data" tick={CHART_AXIS_TICK} tickMargin={8} />
        <YAxis tick={CHART_AXIS_TICK} width={32} domain={[0, 10]} />
        <ReTooltip
          contentStyle={CHART_TOOLTIP_CONTENT_STYLE}
          labelStyle={CHART_TOOLTIP_LABEL_STYLE}
          itemStyle={CHART_TOOLTIP_ITEM_STYLE}
          formatter={(value) => formatFixed(Number(value), 1)}
        />
        <Line type="monotone" dataKey="rpeMedio" name="RPE médio" stroke={CHART_COLORS.secondary} strokeWidth={2} dot={false} activeDot={{ r: 5 }} connectNulls />
      </LineChart>
    </ResponsiveContainer>
  );
}
