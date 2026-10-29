'use client';

// Gráficos (recharts) de recuperação do Intervals.icu, em módulo próprio para
// carga sob demanda (`next/dynamic`, COD-14).
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

// CTL/ATL/TSB compartilham a mesma unidade de "carga de treino" (regra de
// eixo único do dataviz: compatíveis no mesmo gráfico); HRV (ms) vai em
// gráfico separado por ter unidade diferente — mesma decisão da referência
// local em fitness-frontend/features/physical-indicators/RecoveryMetricsSection.tsx.
const TREND_SERIES = [
  { key: 'fitness_ctl', label: 'Aptidão (CTL)', color: CHART_COLORS.primary },
  { key: 'fatigue_atl', label: 'Fadiga (ATL)', color: CHART_COLORS.secondary },
  { key: 'form_tsb', label: 'Forma (TSB)', color: CHART_COLORS.compare },
] as const;

export function TrainingLoadChart({ chartData }: { chartData: ChartRows }) {
  return (
    <ResponsiveContainer width="100%" height={260}>
      <LineChart data={chartData} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
        <CartesianGrid stroke={CHART_GRID_STROKE} strokeDasharray="4 4" vertical={false} />
        <XAxis dataKey="data" tick={CHART_AXIS_TICK} tickMargin={8} />
        <YAxis tick={CHART_AXIS_TICK} width={40} />
        <ReTooltip
          contentStyle={CHART_TOOLTIP_CONTENT_STYLE}
          labelStyle={CHART_TOOLTIP_LABEL_STYLE}
          itemStyle={CHART_TOOLTIP_ITEM_STYLE}
          formatter={(value, name) => {
            const s = TREND_SERIES.find((x) => x.key === String(name));
            return [formatNumberOrDash(Number(value)), s?.label ?? String(name)] as [string, string];
          }}
        />
        <Legend wrapperStyle={CHART_LEGEND_STYLE} formatter={(value) => TREND_SERIES.find((s) => s.key === value)?.label ?? value} />
        {TREND_SERIES.map((s) => (
          <Line key={s.key} type="monotone" dataKey={s.key} name={s.key} stroke={s.color} strokeWidth={2} dot={false} activeDot={{ r: 5 }} connectNulls />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}

export function HrvChart({ chartData }: { chartData: ChartRows }) {
  return (
    <ResponsiveContainer width="100%" height={260}>
      <LineChart data={chartData} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
        <CartesianGrid stroke={CHART_GRID_STROKE} strokeDasharray="4 4" vertical={false} />
        <XAxis dataKey="data" tick={CHART_AXIS_TICK} tickMargin={8} />
        <YAxis tick={CHART_AXIS_TICK} width={40} />
        <ReTooltip
          contentStyle={CHART_TOOLTIP_CONTENT_STYLE}
          labelStyle={CHART_TOOLTIP_LABEL_STYLE}
          itemStyle={CHART_TOOLTIP_ITEM_STYLE}
          formatter={(value) => [formatNumberOrDash(Number(value), 0), 'HRV'] as [string, string]}
        />
        <Line type="monotone" dataKey="hrv_ms" name="HRV" stroke={CHART_COLORS.primary} strokeWidth={2} dot={false} activeDot={{ r: 5 }} connectNulls />
      </LineChart>
    </ResponsiveContainer>
  );
}
