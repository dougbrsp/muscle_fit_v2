'use client';

// Gráficos (recharts) de /intervals-icu, em módulo próprio para carga sob
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
import { formatNumberOrDash } from '@/lib/physical-indicators-types';

const WELLNESS_SERIES = [
  { key: 'fitness', label: 'Fitness (CTL)', color: CHART_COLORS.primary },
  { key: 'fatigue', label: 'Fadiga (ATL)', color: CHART_COLORS.secondary },
  { key: 'form', label: 'Forma (TSB)', color: CHART_COLORS.compare },
] as const;

export function MinutesChart({ minutesChartData }: { minutesChartData: ChartRows }) {
  return (
    <ResponsiveContainer width="100%" height={240}>
      <LineChart data={minutesChartData} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
        <CartesianGrid stroke={CHART_GRID_STROKE} strokeDasharray="4 4" vertical={false} />
        <XAxis dataKey="data" tick={CHART_AXIS_TICK} tickMargin={8} />
        <YAxis tick={CHART_AXIS_TICK} width={40} />
        <ReTooltip contentStyle={CHART_TOOLTIP_CONTENT_STYLE} labelStyle={CHART_TOOLTIP_LABEL_STYLE} itemStyle={CHART_TOOLTIP_ITEM_STYLE} />
        <Line type="monotone" dataKey="minutos" name="Minutos" stroke={CHART_COLORS.primary} strokeWidth={2} dot={false} activeDot={{ r: 5 }} connectNulls />
      </LineChart>
    </ResponsiveContainer>
  );
}

export function LoadChart({ loadChartData }: { loadChartData: ChartRows }) {
  return (
    <ResponsiveContainer width="100%" height={240}>
      <LineChart data={loadChartData} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
        <CartesianGrid stroke={CHART_GRID_STROKE} strokeDasharray="4 4" vertical={false} />
        <XAxis dataKey="data" tick={CHART_AXIS_TICK} tickMargin={8} />
        <YAxis tick={CHART_AXIS_TICK} width={40} />
        <ReTooltip contentStyle={CHART_TOOLTIP_CONTENT_STYLE} labelStyle={CHART_TOOLTIP_LABEL_STYLE} itemStyle={CHART_TOOLTIP_ITEM_STYLE} />
        <Line type="monotone" dataKey="carga" name="Carga" stroke={CHART_COLORS.compare} strokeWidth={2} dot={false} activeDot={{ r: 5 }} connectNulls />
      </LineChart>
    </ResponsiveContainer>
  );
}

export function WellnessChart({ wellnessChartData }: { wellnessChartData: ChartRows }) {
  return (
    <ResponsiveContainer width="100%" height={240}>
      <LineChart data={wellnessChartData} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
        <CartesianGrid stroke={CHART_GRID_STROKE} strokeDasharray="4 4" vertical={false} />
        <XAxis dataKey="data" tick={CHART_AXIS_TICK} tickMargin={8} />
        <YAxis tick={CHART_AXIS_TICK} width={40} />
        <ReTooltip
          contentStyle={CHART_TOOLTIP_CONTENT_STYLE}
          labelStyle={CHART_TOOLTIP_LABEL_STYLE}
          itemStyle={CHART_TOOLTIP_ITEM_STYLE}
          formatter={(value, name) => {
            const s = WELLNESS_SERIES.find((x) => x.key === String(name));
            return [formatNumberOrDash(Number(value)), s?.label ?? String(name)] as [string, string];
          }}
        />
        <Legend wrapperStyle={CHART_LEGEND_STYLE} formatter={(value) => WELLNESS_SERIES.find((s) => s.key === value)?.label ?? value} />
        {WELLNESS_SERIES.map((s) => (
          <Line key={s.key} type="monotone" dataKey={s.key} name={s.key} stroke={s.color} strokeWidth={2} dot={false} activeDot={{ r: 5 }} connectNulls />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}

export function RampRateChart({ rampRateChartData }: { rampRateChartData: ChartRows }) {
  return (
    <ResponsiveContainer width="100%" height={240}>
      <LineChart data={rampRateChartData} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
        <CartesianGrid stroke={CHART_GRID_STROKE} strokeDasharray="4 4" vertical={false} />
        <XAxis dataKey="data" tick={CHART_AXIS_TICK} tickMargin={8} />
        <YAxis tick={CHART_AXIS_TICK} width={40} />
        <ReTooltip contentStyle={CHART_TOOLTIP_CONTENT_STYLE} labelStyle={CHART_TOOLTIP_LABEL_STYLE} itemStyle={CHART_TOOLTIP_ITEM_STYLE} />
        <Line type="monotone" dataKey="rampa" name="Ramp rate" stroke={CHART_COLORS.secondary} strokeWidth={2} dot={false} activeDot={{ r: 5 }} connectNulls />
      </LineChart>
    </ResponsiveContainer>
  );
}
