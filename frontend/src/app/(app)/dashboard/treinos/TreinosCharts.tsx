'use client';

// Gráficos (recharts) dos blocos "Distribuição de RPE" e "Evolução por semana
// ISO". Ficam num módulo próprio para serem carregados sob demanda
// (`next/dynamic`, COD-14): o recharts sai do First Load JS de /dashboard/treinos.
import {
  Bar,
  BarChart,
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
import { fmtInt, fmtKgTotal } from '@/lib/dashboard-format';
import type { isoWeekRows, rpeRows } from '@/lib/dashboard-training-analytics';

// Não exportar: importar daqui de forma estática traria o recharts de volta ao
// First Load JS. O placeholder dos blocos repete 260.
const TREINOS_CHART_HEIGHT = 260;

export function DistribuicaoRpeChart({ linhas }: { linhas: ReturnType<typeof rpeRows> }) {
  return (
    <ResponsiveContainer width="100%" height={TREINOS_CHART_HEIGHT}>
      <BarChart data={linhas} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
        <CartesianGrid stroke={CHART_GRID_STROKE} strokeDasharray="4 4" vertical={false} />
        <XAxis dataKey="rpe" tick={CHART_AXIS_TICK} />
        <YAxis tick={CHART_AXIS_TICK} width={40} allowDecimals={false} />
        <RechartsTooltip
          contentStyle={CHART_TOOLTIP_CONTENT_STYLE}
          labelStyle={CHART_TOOLTIP_LABEL_STYLE}
          itemStyle={CHART_TOOLTIP_ITEM_STYLE}
          formatter={(value) => [fmtInt(Number(value)), 'Séries']}
          labelFormatter={(label) => `RPE ${label}`}
        />
        <Bar dataKey="sets" name="Séries" fill={CHART_COLORS.primary} radius={[4, 4, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}

export function EvolucaoSemanalChart({ linhas }: { linhas: ReturnType<typeof isoWeekRows> }) {
  return (
    <ResponsiveContainer width="100%" height={TREINOS_CHART_HEIGHT}>
      <LineChart data={linhas} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
        <CartesianGrid stroke={CHART_GRID_STROKE} strokeDasharray="4 4" vertical={false} />
        <XAxis dataKey="periodo" tick={CHART_AXIS_TICK} />
        <YAxis tick={CHART_AXIS_TICK} width={56} />
        <RechartsTooltip
          contentStyle={CHART_TOOLTIP_CONTENT_STYLE}
          labelStyle={CHART_TOOLTIP_LABEL_STYLE}
          itemStyle={CHART_TOOLTIP_ITEM_STYLE}
          formatter={(value) => [fmtKgTotal(Number(value)), 'Volume']}
        />
        <Line
          type="monotone"
          dataKey="volume"
          name="Volume"
          stroke={CHART_COLORS.primary}
          strokeWidth={2}
          dot={{ r: 4, fill: CHART_COLORS.primary }}
          activeDot={{ r: 6 }}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
