'use client';

// Gráficos (recharts) da nutrição, em módulo próprio para carga sob demanda
// (`next/dynamic`, COD-14). Só desenham as linhas montadas por
// `lib/nutrition-dashboard.ts`; nenhum cálculo de nutrição aqui. Uma escala por
// gráfico (sem eixo duplo); a identidade nunca depende só da cor: legenda e
// tooltip sempre nomeiam a série, e cada tela mostra os mesmos números em texto.
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
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
import type { BodyRow, RingPart, ShareRow, WeekAdherenceRow, WeekBarRow } from '@/lib/nutrition-dashboard';
import { fmtDecimal } from '@/lib/dashboard-format';

const TOOLTIP = {
  contentStyle: CHART_TOOLTIP_CONTENT_STYLE,
  labelStyle: CHART_TOOLTIP_LABEL_STYLE,
  itemStyle: CHART_TOOLTIP_ITEM_STYLE,
};

// Rosca: consumido (laranja de marca), a consumir (cinza neutro) e acima da meta
// (vermelho de marca). O texto ao lado dá os três números; a cor reforça.
const RING_COLORS: Record<RingPart['key'], string> = {
  within: CHART_COLORS.primary,
  to_consume: CHART_COLORS.compare,
  excess: CHART_COLORS.secondary,
};

export const RING_SIZE = 132;

// Semana com poucos dias registrados: barras esmaecidas (a tabela e o tooltip dizem "semana incompleta").
const INCOMPLETE_WEEK_OPACITY = 0.4;

export function MacroRing({ parts, unit }: { parts: RingPart[]; unit: string }) {
  return (
    <ResponsiveContainer width="100%" height={RING_SIZE}>
      <PieChart>
        <ReTooltip
          {...TOOLTIP}
          formatter={(value, name) => [`${fmtDecimal(Number(value), 0)} ${unit}`, String(name)]}
        />
        <Pie
          data={parts}
          dataKey="value"
          nameKey="label"
          innerRadius="62%"
          outerRadius="92%"
          startAngle={90}
          endAngle={-270}
          paddingAngle={2}
          stroke="none"
          isAnimationActive={false}
        >
          {parts.map((parte) => (
            <Cell key={parte.key} fill={RING_COLORS[parte.key]} />
          ))}
        </Pie>
      </PieChart>
    </ResponsiveContainer>
  );
}

export function WeekBars({ rows }: { rows: WeekBarRow[] }) {
  return (
    <ResponsiveContainer width="100%" height={200}>
      <BarChart data={rows} margin={{ top: 8, right: 8, left: 0, bottom: 4 }} barGap={2}>
        <CartesianGrid stroke={CHART_GRID_STROKE} strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="label" tick={CHART_AXIS_TICK} />
        <YAxis tick={CHART_AXIS_TICK} width={44} />
        <ReTooltip {...TOOLTIP} formatter={(value, name) => [`${fmtDecimal(Number(value), 0)} kcal`, String(name)]} />
        <Legend wrapperStyle={CHART_LEGEND_STYLE} />
        <Bar dataKey="consumido" name="Consumido" fill={CHART_COLORS.primary} radius={[4, 4, 0, 0]} maxBarSize={22} />
        <Bar dataKey="meta" name="Meta" fill={CHART_COLORS.compare} radius={[4, 4, 0, 0]} maxBarSize={22} />
      </BarChart>
    </ResponsiveContainer>
  );
}

/** Fração da meta por dia (%), com a linha dos 100%. Uma série: o título do cartão a nomeia. */
export function ShareBars({ rows, name }: { rows: ShareRow[]; name: string }) {
  return (
    <ResponsiveContainer width="100%" height={180}>
      <BarChart data={rows} margin={{ top: 8, right: 8, left: 0, bottom: 4 }}>
        <CartesianGrid stroke={CHART_GRID_STROKE} strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="label" tick={CHART_AXIS_TICK} interval="preserveStartEnd" minTickGap={24} />
        <YAxis tick={CHART_AXIS_TICK} width={44} unit="%" />
        <ReTooltip
          {...TOOLTIP}
          formatter={(value, _name, item) => {
            const linha = item.payload as ShareRow;
            return [`${fmtDecimal(Number(value), 0)}% da meta${linha.incompleto ? ' (dia incompleto)' : ''}`, name];
          }}
        />
        <ReferenceLine y={100} stroke={CHART_COLORS.compare} strokeDasharray="4 4" ifOverflow="extendDomain" />
        <Bar dataKey="percent" name={name} fill={CHART_COLORS.primary} radius={[3, 3, 0, 0]} maxBarSize={14} />
      </BarChart>
    </ResponsiveContainer>
  );
}

export function WeekAdherenceBars({ rows }: { rows: WeekAdherenceRow[] }) {
  return (
    <ResponsiveContainer width="100%" height={220}>
      <BarChart data={rows} margin={{ top: 8, right: 8, left: 0, bottom: 4 }} barGap={2}>
        <CartesianGrid stroke={CHART_GRID_STROKE} strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="label" tick={CHART_AXIS_TICK} />
        <YAxis tick={CHART_AXIS_TICK} width={44} unit="%" />
        <ReTooltip
          {...TOOLTIP}
          labelFormatter={(label, items) => {
            const linha = items?.[0]?.payload as WeekAdherenceRow | undefined;
            return linha
              ? `Semana de ${label}${linha.parcial ? ' (parcial)' : ''}${linha.valida ? '' : ' (semana incompleta)'} · ${linha.diasRegistrados} dia(s) registrado(s)`
              : String(label);
          }}
          formatter={(value, name) => [`${fmtDecimal(Number(value), 0)}% da meta`, String(name)]}
        />
        <Legend wrapperStyle={CHART_LEGEND_STYLE} />
        <ReferenceLine y={100} stroke={CHART_COLORS.compare} strokeDasharray="4 4" ifOverflow="extendDomain" />
        <Bar dataKey="kcal" name="Energia (kcal)" fill={CHART_COLORS.primary} radius={[4, 4, 0, 0]} maxBarSize={28}>
          {rows.map((linha) => (
            <Cell key={linha.start} fillOpacity={linha.valida ? 1 : INCOMPLETE_WEEK_OPACITY} />
          ))}
        </Bar>
        <Bar dataKey="proteina" name="Proteína" fill={CHART_COLORS.compare} radius={[4, 4, 0, 0]} maxBarSize={28}>
          {rows.map((linha) => (
            <Cell key={linha.start} fillOpacity={linha.valida ? 1 : INCOMPLETE_WEEK_OPACITY} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

type BodyChartProps = {
  rows: BodyRow[];
  unit: string;
  /** Alvo do plano (peso-alvo ou %G alvo), como linha de referência. */
  goal?: number | null;
  goalLabel?: string;
};

function BodyAxis({ unit }: { unit: string }) {
  return <YAxis domain={['auto', 'auto']} tick={CHART_AXIS_TICK} width={48} unit={` ${unit}`} />;
}

export function WeightChart({ rows, unit, goal, goalLabel }: BodyChartProps) {
  const dados = rows.filter((linha) => linha.peso != null || linha.media != null);
  return (
    <ResponsiveContainer width="100%" height={240}>
      <LineChart data={dados} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
        <CartesianGrid stroke={CHART_GRID_STROKE} strokeDasharray="3 3" />
        <XAxis dataKey="label" tick={CHART_AXIS_TICK} interval="preserveStartEnd" minTickGap={24} />
        <BodyAxis unit={unit} />
        <ReTooltip {...TOOLTIP} formatter={(value, name) => [`${fmtDecimal(Number(value), 1)} ${unit}`, String(name)]} />
        <Legend wrapperStyle={CHART_LEGEND_STYLE} />
        {goal != null && (
          <ReferenceLine y={goal} stroke={CHART_COLORS.secondary} strokeDasharray="4 4" ifOverflow="extendDomain" label={{ value: goalLabel ?? 'Alvo', fill: CHART_AXIS_TICK.fill, fontSize: 12, position: 'insideTopRight' }} />
        )}
        <Line type="monotone" dataKey="peso" name="Pesagem" stroke={CHART_COLORS.compare} strokeWidth={1} dot={{ r: 2 }} connectNulls />
        <Line type="monotone" dataKey="media" name="Média móvel" stroke={CHART_COLORS.primary} strokeWidth={2} dot={false} connectNulls />
      </LineChart>
    </ResponsiveContainer>
  );
}

export function SingleSeriesChart({ rows, unit, dataKey, name, goal, goalLabel }: BodyChartProps & { dataKey: 'gordura' | 'musculo'; name: string }) {
  const dados = rows.filter((linha) => linha[dataKey] != null);
  return (
    <ResponsiveContainer width="100%" height={200}>
      <LineChart data={dados} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
        <CartesianGrid stroke={CHART_GRID_STROKE} strokeDasharray="3 3" />
        <XAxis dataKey="label" tick={CHART_AXIS_TICK} interval="preserveStartEnd" minTickGap={24} />
        <BodyAxis unit={unit} />
        <ReTooltip {...TOOLTIP} formatter={(value) => [`${fmtDecimal(Number(value), 1)} ${unit} · estimado`, name]} />
        {goal != null && (
          <ReferenceLine y={goal} stroke={CHART_COLORS.secondary} strokeDasharray="4 4" ifOverflow="extendDomain" label={{ value: goalLabel ?? 'Alvo', fill: CHART_AXIS_TICK.fill, fontSize: 12, position: 'insideTopRight' }} />
        )}
        <Line type="monotone" dataKey={dataKey} name={name} stroke={CHART_COLORS.primary} strokeWidth={2} dot={{ r: 3 }} connectNulls />
      </LineChart>
    </ResponsiveContainer>
  );
}
