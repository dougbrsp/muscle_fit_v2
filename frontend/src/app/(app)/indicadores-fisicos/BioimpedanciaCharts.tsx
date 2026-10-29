'use client';

// Gráficos (recharts) da bioimpedância, em módulo próprio para carga sob
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

function formatKgTick(value: number | null | undefined) {
  const n = Number(value);
  return Number.isFinite(n) ? `${n.toFixed(1)} kg` : '-';
}

function formatPercentTick(value: number | null | undefined) {
  const n = Number(value);
  return Number.isFinite(n) ? `${n.toFixed(1)}%` : '-';
}

export function MassChart({ massChartData }: { massChartData: ChartRows }) {
  return (
    <ResponsiveContainer width="100%" height={260}>
      <LineChart data={massChartData} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
        <CartesianGrid stroke={CHART_GRID_STROKE} strokeDasharray="4 4" vertical={false} />
        <XAxis dataKey="data" tick={CHART_AXIS_TICK} tickMargin={8} />
        <YAxis tick={CHART_AXIS_TICK} width={48} />
        <ReTooltip
          contentStyle={CHART_TOOLTIP_CONTENT_STYLE}
          labelStyle={CHART_TOOLTIP_LABEL_STYLE}
          itemStyle={CHART_TOOLTIP_ITEM_STYLE}
          formatter={(value) => formatKgTick(Number(value))}
        />
        <Legend wrapperStyle={CHART_LEGEND_STYLE} />
        <Line type="monotone" dataKey="peso" name="Peso" stroke={CHART_COLORS.primary} strokeWidth={2} dot={false} activeDot={{ r: 5 }} connectNulls />
        <Line type="monotone" dataKey="massaMuscular" name="Massa muscular" stroke={CHART_COLORS.compare} strokeWidth={2} dot={false} activeDot={{ r: 5 }} connectNulls />
      </LineChart>
    </ResponsiveContainer>
  );
}

export function PercentChart({ percentChartData }: { percentChartData: ChartRows }) {
  return (
    <ResponsiveContainer width="100%" height={260}>
      <LineChart data={percentChartData} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
        <CartesianGrid stroke={CHART_GRID_STROKE} strokeDasharray="4 4" vertical={false} />
        <XAxis dataKey="data" tick={CHART_AXIS_TICK} tickMargin={8} />
        <YAxis tick={CHART_AXIS_TICK} width={40} />
        <ReTooltip
          contentStyle={CHART_TOOLTIP_CONTENT_STYLE}
          labelStyle={CHART_TOOLTIP_LABEL_STYLE}
          itemStyle={CHART_TOOLTIP_ITEM_STYLE}
          formatter={(value) => formatPercentTick(Number(value))}
        />
        <Legend wrapperStyle={CHART_LEGEND_STYLE} />
        <Line type="monotone" dataKey="gordura" name="Gordura" stroke={CHART_COLORS.secondary} strokeWidth={2} dot={false} activeDot={{ r: 5 }} connectNulls />
        <Line type="monotone" dataKey="agua" name="Água" stroke={CHART_COLORS.compare} strokeWidth={2} dot={false} activeDot={{ r: 5 }} connectNulls />
      </LineChart>
    </ResponsiveContainer>
  );
}
