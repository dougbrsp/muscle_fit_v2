'use client';

// Gráficos (recharts) de contexto do /agente-cardio, em módulo próprio para
// carga sob demanda (`next/dynamic`, COD-14).
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip as ReTooltip,
  XAxis,
  YAxis,
} from 'recharts';
import {
  CHART_AXIS_TICK,
  CHART_GRID_STROKE,
  CHART_LEGEND_STYLE,
  CHART_TOOLTIP_CONTENT_STYLE,
  CHART_TOOLTIP_ITEM_STYLE,
  CHART_TOOLTIP_LABEL_STYLE,
  type ChartRows,
} from '@/components/charts/chartTheme';
import { BRAND } from '@/theme';

// ─── Séries dos gráficos de contexto (mesma paleta semântica de
// evolucao-corporal/recuperacao-muscular — CHART_COLORS não cobre 3 séries
// simultâneas, então usamos cores fixas dedicadas por bloco). ───────────────

const WELLNESS_SERIES = [
  { key: 'fitness_ctl', label: 'Aptidão (CTL)', color: BRAND.orange },
  { key: 'fatigue_atl', label: 'Fadiga (ATL)', color: BRAND.red },
  { key: 'form_tsb', label: 'Forma (TSB)', color: '#2f9e44' },
] as const;

const SLEEP_SERIES = [
  { key: 'duration_hours', label: 'Duração (h)', color: BRAND.orange },
  { key: 'deep_sleep_hours', label: 'Sono profundo (h)', color: '#8B93A6' },
] as const;

const EFFORT_SERIES = [
  { key: 'epoc_7d_estimated', label: 'EPOC 7 dias (estimado)', color: BRAND.orange },
  { key: 'pai_7d_estimated', label: 'PAI 7 dias (estimado)', color: '#8B93A6' },
] as const;

export function WellnessChart({ wellnessChartData }: { wellnessChartData: ChartRows }) {
  return (
    <ResponsiveContainer width="100%" height={260}>
      <LineChart data={wellnessChartData} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
        <CartesianGrid stroke={CHART_GRID_STROKE} strokeDasharray="4 4" vertical={false} />
        <XAxis dataKey="date" tick={CHART_AXIS_TICK} tickMargin={8} />
        <YAxis tick={CHART_AXIS_TICK} width={40} />
        <ReTooltip
          contentStyle={CHART_TOOLTIP_CONTENT_STYLE}
          labelStyle={CHART_TOOLTIP_LABEL_STYLE}
          itemStyle={CHART_TOOLTIP_ITEM_STYLE}
          formatter={(value, name) => {
            const s = WELLNESS_SERIES.find((x) => x.key === String(name));
            return [Number(value).toFixed(1), s?.label ?? String(name)] as [string, string];
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

export function SleepChart({ sleepChartData }: { sleepChartData: ChartRows }) {
  return (
    <ResponsiveContainer width="100%" height={220}>
      <LineChart data={sleepChartData} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
        <CartesianGrid stroke={CHART_GRID_STROKE} strokeDasharray="4 4" vertical={false} />
        <XAxis dataKey="date" tick={CHART_AXIS_TICK} tickMargin={8} />
        <YAxis tick={CHART_AXIS_TICK} width={32} />
        <ReTooltip
          contentStyle={CHART_TOOLTIP_CONTENT_STYLE}
          labelStyle={CHART_TOOLTIP_LABEL_STYLE}
          itemStyle={CHART_TOOLTIP_ITEM_STYLE}
          formatter={(value, name) => {
            const s = SLEEP_SERIES.find((x) => x.key === String(name));
            return [Number(value).toFixed(1), s?.label ?? String(name)] as [string, string];
          }}
        />
        <Legend wrapperStyle={CHART_LEGEND_STYLE} formatter={(value) => SLEEP_SERIES.find((s) => s.key === value)?.label ?? value} />
        {SLEEP_SERIES.map((s) => (
          <Line key={s.key} type="monotone" dataKey={s.key} name={s.key} stroke={s.color} strokeWidth={2} dot={false} activeDot={{ r: 5 }} connectNulls />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}

export function EffortChart({ effortChartData }: { effortChartData: ChartRows }) {
  return (
    <ResponsiveContainer width="100%" height={220}>
      <LineChart data={effortChartData} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
        <CartesianGrid stroke={CHART_GRID_STROKE} strokeDasharray="4 4" vertical={false} />
        <XAxis dataKey="date" tick={CHART_AXIS_TICK} tickMargin={8} />
        <YAxis tick={CHART_AXIS_TICK} width={32} />
        <ReTooltip
          contentStyle={CHART_TOOLTIP_CONTENT_STYLE}
          labelStyle={CHART_TOOLTIP_LABEL_STYLE}
          itemStyle={CHART_TOOLTIP_ITEM_STYLE}
          formatter={(value, name) => {
            const s = EFFORT_SERIES.find((x) => x.key === String(name));
            return [Number(value).toFixed(1), s?.label ?? String(name)] as [string, string];
          }}
        />
        <Legend wrapperStyle={CHART_LEGEND_STYLE} formatter={(value) => EFFORT_SERIES.find((s) => s.key === value)?.label ?? value} />
        {EFFORT_SERIES.map((s) => (
          <Line key={s.key} type="monotone" dataKey={s.key} name={s.key} stroke={s.color} strokeWidth={2} dot={false} activeDot={{ r: 5 }} connectNulls />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}
