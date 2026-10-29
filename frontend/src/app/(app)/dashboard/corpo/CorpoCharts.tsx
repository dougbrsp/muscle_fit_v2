'use client';

// Gráficos (recharts) do dashboard de corpo, em módulo próprio para carga sob
// demanda (`next/dynamic`, COD-14).
import {
  CartesianGrid,
  ComposedChart,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  Tooltip as ReTooltip,
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
import {
  dateToTime,
  type measurementChartRows,
  type performanceChartRows,
  type weightChartRows,
} from '@/lib/dashboard-body';
import type { BodyMilestone } from '@/lib/dashboard-body-types';
import { fmtDate, fmtDecimal } from '@/lib/dashboard-format';

// Repetida no placeholder de DashboardCorpoClient (240).
const CHART_HEIGHT = 240;

function tickDate(value: number): string {
  return fmtDate(new Date(value).toISOString().slice(0, 10));
}

export function PesoChart({ rows, dominio, marcos }: { rows: ReturnType<typeof weightChartRows>; dominio: [number, number]; marcos: BodyMilestone[] }) {
  return (
    <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
      <LineChart data={rows} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
        <CartesianGrid stroke={CHART_GRID_STROKE} strokeDasharray="3 3" />
        <XAxis dataKey="t" type="number" domain={dominio} tickFormatter={tickDate} tick={CHART_AXIS_TICK} />
        <YAxis domain={['auto', 'auto']} tick={CHART_AXIS_TICK} width={48} unit=" kg" />
        <ReTooltip
          contentStyle={CHART_TOOLTIP_CONTENT_STYLE}
          labelStyle={CHART_TOOLTIP_LABEL_STYLE}
          itemStyle={CHART_TOOLTIP_ITEM_STYLE}
          labelFormatter={(value) => tickDate(Number(value))}
          formatter={(value, name, item) => {
            const row = item.payload as (typeof rows)[number];
            if (name === 'Média 7 dias') return [`${fmtDecimal(Number(value), 1)} kg (${row.pontos} pesagens)`, name];
            return [`${fmtDecimal(Number(value), 1)} kg · ${row.fonte}`, name];
          }}
        />
        <Line type="monotone" dataKey="peso" name="Pesagem" stroke={CHART_COLORS.compare} strokeWidth={1} dot={{ r: 2 }} />
        <Line type="monotone" dataKey="media7" name="Média 7 dias" stroke={CHART_COLORS.primary} strokeWidth={2} dot={false} connectNulls />
        {marcos.map((m) => (
          <ReferenceLine key={`${m.kind}-${m.local_date}`} x={dateToTime(m.local_date)} stroke={CHART_COLORS.secondary} strokeDasharray="4 4" />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}

export function DesempenhoChart({ rows, dominio }: { rows: ReturnType<typeof performanceChartRows>; dominio: [number, number] }) {
  return (
    <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
      <LineChart data={rows} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
        <CartesianGrid stroke={CHART_GRID_STROKE} strokeDasharray="3 3" />
        <XAxis dataKey="t" type="number" domain={dominio} tickFormatter={tickDate} tick={CHART_AXIS_TICK} />
        <YAxis domain={['auto', 'auto']} tick={CHART_AXIS_TICK} width={48} unit=" kg" />
        <ReTooltip
          contentStyle={CHART_TOOLTIP_CONTENT_STYLE}
          labelStyle={CHART_TOOLTIP_LABEL_STYLE}
          itemStyle={CHART_TOOLTIP_ITEM_STYLE}
          labelFormatter={(value) => tickDate(Number(value))}
          formatter={(value, name, item) => {
            const row = item.payload as (typeof rows)[number];
            return [`${fmtDecimal(Number(value), 1)} kg (${row.exercicios} exercícios) · estimado`, name];
          }}
        />
        <Line type="monotone" dataKey="e1rm" name="e1RM médio" stroke={CHART_COLORS.primary} strokeWidth={2} dot={{ r: 2 }} />
      </LineChart>
    </ResponsiveContainer>
  );
}

export function MedidaChart({ rows, dominio }: { rows: ReturnType<typeof measurementChartRows>; dominio: [number, number] }) {
  return (
    <ResponsiveContainer width="100%" height={CHART_HEIGHT}>
      <ComposedChart data={rows} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
        <CartesianGrid stroke={CHART_GRID_STROKE} strokeDasharray="3 3" />
        <XAxis dataKey="t" type="number" domain={dominio} tickFormatter={tickDate} tick={CHART_AXIS_TICK} />
        <YAxis domain={['auto', 'auto']} tick={CHART_AXIS_TICK} width={48} unit=" cm" />
        <ReTooltip
          contentStyle={CHART_TOOLTIP_CONTENT_STYLE}
          labelStyle={CHART_TOOLTIP_LABEL_STYLE}
          itemStyle={CHART_TOOLTIP_ITEM_STYLE}
          labelFormatter={(value) => tickDate(Number(value))}
          formatter={(value, name, item) => {
            const row = item.payload as (typeof rows)[number];
            const nota = row.notas ? ` · ${row.notas}` : '';
            return [`${fmtDecimal(Number(value), 1)} cm${nota}`, name];
          }}
        />
        <Line type="monotone" dataKey="protocolo" name="No protocolo" stroke={CHART_COLORS.primary} strokeWidth={2} dot={{ r: 3 }} connectNulls />
        <Scatter dataKey="foraProtocolo" name="Fora do protocolo (LOW)" fill={CHART_COLORS.compare} />
      </ComposedChart>
    </ResponsiveContainer>
  );
}
