'use client';

// Gráficos (recharts) de `/dashboard/cardio` (M98 C4), em módulo próprio para
// carga sob demanda (`next/dynamic`, COD-14). Só desenham linhas já prontas
// (`lib/dashboard-cardio.ts`): nenhum número é calculado aqui.
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip as ReTooltip,
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
import { ZONE_KEYS, type WeekModeRow, type WeekZoneRow, type WellnessRow } from '@/lib/dashboard-cardio';
import { fmtDecimal, fmtInt } from '@/lib/dashboard-format';
import { BRAND, WORKOUT_MODE_TOKENS } from '@/theme';

export const CARDIO_CHART_HEIGHT = 240;

// Escala fria → quente das zonas (Z1 neutra, Z5 vermelho de marca). A zona
// também aparece escrita na legenda e no tooltip: cor nunca é o único sinal.
const ZONE_COLORS: Record<(typeof ZONE_KEYS)[number], string> = {
  Z1: CHART_COLORS.compare,
  Z2: CHART_COLORS.positive,
  Z3: WORKOUT_MODE_TOKENS.warning,
  Z4: BRAND.orange,
  Z5: BRAND.red,
};

const MARGIN = { top: 8, right: 16, left: 0, bottom: 8 };

function minutos(value: unknown): string {
  return `${fmtInt(Number(value))} min`;
}

export function ZonasSemanaChart({ rows }: { rows: WeekZoneRow[] }) {
  return (
    <ResponsiveContainer width="100%" height={CARDIO_CHART_HEIGHT}>
      <BarChart data={rows} margin={MARGIN}>
        <CartesianGrid stroke={CHART_GRID_STROKE} strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="semana" tick={CHART_AXIS_TICK} />
        <YAxis tick={CHART_AXIS_TICK} width={44} unit=" min" />
        <ReTooltip
          contentStyle={CHART_TOOLTIP_CONTENT_STYLE}
          labelStyle={CHART_TOOLTIP_LABEL_STYLE}
          itemStyle={CHART_TOOLTIP_ITEM_STYLE}
          labelFormatter={(label) => `Semana de ${label}`}
          formatter={(value, name) => [minutos(value), String(name)]}
        />
        <Legend wrapperStyle={CHART_LEGEND_STYLE} />
        {ZONE_KEYS.map((zone) => (
          <Bar key={zone} dataKey={zone} name={zone} stackId="zonas" fill={ZONE_COLORS[zone]} />
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
}

export function ModoSemanaChart({ rows }: { rows: WeekModeRow[] }) {
  return (
    <ResponsiveContainer width="100%" height={CARDIO_CHART_HEIGHT}>
      <BarChart data={rows} margin={MARGIN}>
        <CartesianGrid stroke={CHART_GRID_STROKE} strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="semana" tick={CHART_AXIS_TICK} />
        <YAxis tick={CHART_AXIS_TICK} width={44} unit=" min" />
        <ReTooltip
          contentStyle={CHART_TOOLTIP_CONTENT_STYLE}
          labelStyle={CHART_TOOLTIP_LABEL_STYLE}
          itemStyle={CHART_TOOLTIP_ITEM_STYLE}
          labelFormatter={(label) => `Semana de ${label}`}
          formatter={(value, name, item) => {
            const row = item.payload as WeekModeRow;
            const sessoes =
              name === 'Contínuo' ? row.sessoesContinuo : name === 'Intensa (HIIT)' ? row.sessoesIntensa : row.sessoesSemClasse;
            return [`${minutos(value)} · ${fmtInt(sessoes)} sessão(ões)`, String(name)];
          }}
        />
        <Legend wrapperStyle={CHART_LEGEND_STYLE} />
        <Bar dataKey="continuo" name="Contínuo" stackId="modo" fill={CHART_COLORS.positive} />
        <Bar dataKey="intensa" name="Intensa (HIIT)" stackId="modo" fill={CHART_COLORS.secondary} />
        <Bar dataKey="semClasse" name="Sem tempo em zona" stackId="modo" fill={CHART_COLORS.compare} />
      </BarChart>
    </ResponsiveContainer>
  );
}

export function CargaChart({ rows }: { rows: WellnessRow[] }) {
  return (
    <ResponsiveContainer width="100%" height={CARDIO_CHART_HEIGHT}>
      <LineChart data={rows} margin={MARGIN}>
        <CartesianGrid stroke={CHART_GRID_STROKE} strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="data" tick={CHART_AXIS_TICK} minTickGap={16} />
        <YAxis tick={CHART_AXIS_TICK} width={40} />
        <ReferenceLine y={0} stroke={CHART_GRID_STROKE} />
        <ReTooltip
          contentStyle={CHART_TOOLTIP_CONTENT_STYLE}
          labelStyle={CHART_TOOLTIP_LABEL_STYLE}
          itemStyle={CHART_TOOLTIP_ITEM_STYLE}
          formatter={(value, name) => [fmtDecimal(Number(value), 1), String(name)]}
        />
        <Legend wrapperStyle={CHART_LEGEND_STYLE} />
        <Line type="monotone" dataKey="ctl" name="Aptidão (CTL)" stroke={CHART_COLORS.primary} strokeWidth={2} dot={false} connectNulls />
        <Line type="monotone" dataKey="atl" name="Fadiga (ATL)" stroke={CHART_COLORS.secondary} strokeWidth={2} dot={false} connectNulls />
        <Line
          type="monotone"
          dataKey="tsb"
          name="Forma (TSB)"
          stroke={CHART_COLORS.positive}
          strokeWidth={2}
          strokeDasharray="5 3"
          dot={false}
          connectNulls
        />
      </LineChart>
    </ResponsiveContainer>
  );
}

export function RecuperacaoChart({ rows }: { rows: WellnessRow[] }) {
  return (
    <ResponsiveContainer width="100%" height={CARDIO_CHART_HEIGHT}>
      <LineChart data={rows} margin={MARGIN}>
        <CartesianGrid stroke={CHART_GRID_STROKE} strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="data" tick={CHART_AXIS_TICK} minTickGap={16} />
        <YAxis yAxisId="hrv" tick={CHART_AXIS_TICK} width={44} unit=" ms" domain={['auto', 'auto']} />
        <YAxis yAxisId="fc" orientation="right" tick={CHART_AXIS_TICK} width={52} unit=" bpm" domain={['auto', 'auto']} />
        <ReTooltip
          contentStyle={CHART_TOOLTIP_CONTENT_STYLE}
          labelStyle={CHART_TOOLTIP_LABEL_STYLE}
          itemStyle={CHART_TOOLTIP_ITEM_STYLE}
          formatter={(value, name) => [
            name === 'HRV' ? `${fmtDecimal(Number(value), 0)} ms` : `${fmtDecimal(Number(value), 0)} bpm`,
            String(name),
          ]}
        />
        <Legend wrapperStyle={CHART_LEGEND_STYLE} />
        <Line yAxisId="hrv" type="monotone" dataKey="hrv" name="HRV" stroke={CHART_COLORS.primary} strokeWidth={2} dot={{ r: 2 }} connectNulls />
        <Line
          yAxisId="fc"
          type="monotone"
          dataKey="fcRepouso"
          name="FC de repouso"
          stroke={CHART_COLORS.secondary}
          strokeWidth={2}
          strokeDasharray="5 3"
          dot={{ r: 2 }}
          connectNulls
        />
      </LineChart>
    </ResponsiveContainer>
  );
}

export function PaiChart({ rows, target }: { rows: { data: string; pai: number }[]; target: number }) {
  return (
    <ResponsiveContainer width="100%" height={CARDIO_CHART_HEIGHT}>
      <LineChart data={rows} margin={MARGIN}>
        <CartesianGrid stroke={CHART_GRID_STROKE} strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="data" tick={CHART_AXIS_TICK} minTickGap={16} />
        <YAxis tick={CHART_AXIS_TICK} width={40} domain={[0, 'auto']} />
        <ReTooltip
          contentStyle={CHART_TOOLTIP_CONTENT_STYLE}
          labelStyle={CHART_TOOLTIP_LABEL_STYLE}
          itemStyle={CHART_TOOLTIP_ITEM_STYLE}
          formatter={(value, name) => [fmtInt(Number(value)), String(name)]}
        />
        <ReferenceLine
          y={target}
          stroke={CHART_COLORS.compare}
          strokeDasharray="4 4"
          label={{ value: `Meta ${fmtInt(target)}`, fill: BRAND.textMuted, fontSize: 12, position: 'insideTopRight' }}
        />
        <Line type="monotone" dataKey="pai" name="PAI 7 dias (estimado)" stroke={CHART_COLORS.primary} strokeWidth={2} dot={{ r: 2 }} />
      </LineChart>
    </ResponsiveContainer>
  );
}
