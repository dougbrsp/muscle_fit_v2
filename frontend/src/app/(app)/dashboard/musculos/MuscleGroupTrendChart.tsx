'use client';

// Gráfico de linhas da "Tendência por grupo muscular", em módulo próprio para
// ser carregado sob demanda (`next/dynamic`, COD-14).
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import {
  CHART_AXIS_TICK,
  CHART_COLORS,
  CHART_GRID_STROKE,
  CHART_LEGEND_STYLE,
  CHART_TOOLTIP_CONTENT_STYLE,
  CHART_TOOLTIP_ITEM_STYLE,
  CHART_TOOLTIP_LABEL_STYLE,
} from '@/components/charts/chartTheme';
import { formatTrendMetric, type MuscleGroupTrendFilters, type pivotTrend } from '@/lib/muscle-group-trend';

// Ordem categórica fixa (marca primeiro, depois acentos neutros distintos) —
// herdada da antiga /grupos-musculares.
const MUSCLE_GROUP_LINE_COLORS = [
  CHART_COLORS.primary,
  CHART_COLORS.secondary,
  '#4DA3FF',
  '#8B93A6',
  '#2f9e44',
  '#B98CFF',
];

export function MuscleGroupTrendChart({
  chartPoints,
  visibleGroups,
  metric,
}: {
  chartPoints: ReturnType<typeof pivotTrend>;
  visibleGroups: string[];
  metric: MuscleGroupTrendFilters['metric'];
}) {
  return (
    <ResponsiveContainer width="100%" height={300}>
      <LineChart data={chartPoints} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
        <CartesianGrid stroke={CHART_GRID_STROKE} strokeDasharray="4 4" vertical={false} />
        <XAxis dataKey="period" tick={CHART_AXIS_TICK} tickMargin={8} />
        <YAxis tick={CHART_AXIS_TICK} width={48} />
        <Tooltip
          contentStyle={CHART_TOOLTIP_CONTENT_STYLE}
          labelStyle={CHART_TOOLTIP_LABEL_STYLE}
          itemStyle={CHART_TOOLTIP_ITEM_STYLE}
          formatter={(value) => formatTrendMetric(Number(value), metric)}
        />
        <Legend wrapperStyle={CHART_LEGEND_STYLE} />
        {visibleGroups.map((group, index) => (
          <Line
            key={group}
            type="monotone"
            dataKey={group}
            name={group}
            stroke={MUSCLE_GROUP_LINE_COLORS[index % MUSCLE_GROUP_LINE_COLORS.length]}
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 5 }}
            connectNulls
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}
