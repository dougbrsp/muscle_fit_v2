'use client';

// Gráficos (recharts) de /volume-grupamentos, em módulo próprio para carga sob
// demanda (`next/dynamic`, COD-14).
import { Text } from '@mantine/core';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  Treemap,
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
  type ChartRows,
} from '@/components/charts/chartTheme';
import { formatFixed, formatKgFixed, formatSetsCount } from '@/lib/format';
import { BRAND } from '@/theme';

// Bloco 5: mapa de calor — gradiente vermelho -> amarelo -> verde por
// volume, mesma paleta da referência on-premise (`getHeatmapColor`).
const HEATMAP_COLOR_LOW = { r: 0xe0, g: 0x0d, b: 0x02 };
const HEATMAP_COLOR_MID = { r: 0xe0, g: 0xd5, b: 0x02 };
const HEATMAP_COLOR_HIGH = { r: 0x05, g: 0xab, b: 0x10 };

function toHex(color: { r: number; g: number; b: number }): string {
  return `#${color.r.toString(16).padStart(2, '0')}${color.g.toString(16).padStart(2, '0')}${color.b.toString(16).padStart(2, '0')}`;
}

function getHeatmapColor(value: number | null, minValue: number, maxValue: number): string {
  if (value == null) return '#5C5C5C';
  if (maxValue <= minValue) return toHex(HEATMAP_COLOR_LOW);

  const ratio = Math.min(1, Math.max(0, (value - minValue) / (maxValue - minValue)));
  const [from, to, localRatio] =
    ratio < 0.5
      ? [HEATMAP_COLOR_LOW, HEATMAP_COLOR_MID, ratio / 0.5]
      : [HEATMAP_COLOR_MID, HEATMAP_COLOR_HIGH, (ratio - 0.5) / 0.5];

  const r = Math.round(from.r + (to.r - from.r) * localRatio);
  const g = Math.round(from.g + (to.g - from.g) * localRatio);
  const b = Math.round(from.b + (to.b - from.b) * localRatio);
  return toHex({ r, g, b });
}

function HeatmapCellContent(props: any) {
  const { x, y, width, height, name, sets_count, volume_kg, minVolume, maxVolume } = props;
  if (width <= 0 || height <= 0) return null;

  const color = getHeatmapColor(volume_kg ?? null, minVolume, maxVolume);
  const showLabel = width > 50 && height > 28;

  return (
    <g>
      <rect x={x} y={y} width={width} height={height} rx={6} style={{ fill: color, stroke: BRAND.bg, strokeWidth: 2 }} />
      {showLabel && (
        <>
          <text x={x + width / 2} y={y + height / 2 - 6} textAnchor="middle" style={{ fill: '#0A0A0A' }} fontSize={13} fontWeight={700}>
            {name}
          </text>
          <text x={x + width / 2} y={y + height / 2 + 12} textAnchor="middle" style={{ fill: '#0A0A0A' }} fontSize={11}>
            {sets_count} séries
          </text>
        </>
      )}
    </g>
  );
}

function HeatmapTooltip({ active, payload }: any) {
  if (!active || !payload || !payload.length) return null;
  const data = payload[0]?.payload;
  if (!data) return null;

  return (
    <div style={{ ...CHART_TOOLTIP_CONTENT_STYLE, padding: '8px 12px' }}>
      <Text size="sm" fw={700} c={BRAND.textPrimary}>{data.name}</Text>
      <Text size="xs" c={BRAND.textMuted}>Séries: {formatSetsCount(data.sets_count)}</Text>
      <Text size="xs" c={BRAND.textMuted}>Volume: {formatKgFixed(data.volume_kg, 0)}</Text>
      <Text size="xs" c={BRAND.textMuted}>Carga média: {formatKgFixed(data.avg_weight_kg, 1)}</Text>
    </div>
  );
}

// Bloco 6: sparkline por linha nos comparativos — mini-LineChart do
// recharts, sem eixo/grid, 100x32 (o placeholder da tela tem o mesmo tamanho).
export function SparklineChart({ values, color }: { values: Array<number | null>; color: string }) {
  const data = values.map((value, index) => ({ index, value: value ?? 0 }));

  return (
    <LineChart width={100} height={32} data={data} margin={{ top: 4, right: 4, bottom: 4, left: 4 }}>
      <Line type="monotone" dataKey="value" stroke={color} strokeWidth={2} dot={{ r: 2 }} isAnimationActive={false} />
    </LineChart>
  );
}

export function VolumeByGroupChart({ volumeByGroupChartData }: { volumeByGroupChartData: ChartRows }) {
  return (
    <ResponsiveContainer width="100%" height={280}>
      <BarChart data={volumeByGroupChartData} margin={{ top: 8, right: 16, left: 0, bottom: 48 }}>
        <CartesianGrid stroke={CHART_GRID_STROKE} strokeDasharray="4 4" vertical={false} />
        <XAxis dataKey="grupo" tick={CHART_AXIS_TICK} interval={0} angle={-30} textAnchor="end" height={70} />
        <YAxis tick={CHART_AXIS_TICK} width={48} />
        <Tooltip
          contentStyle={CHART_TOOLTIP_CONTENT_STYLE}
          labelStyle={CHART_TOOLTIP_LABEL_STYLE}
          itemStyle={CHART_TOOLTIP_ITEM_STYLE}
          formatter={(value) => formatKgFixed(Number(value), 0)}
        />
        <Legend wrapperStyle={CHART_LEGEND_STYLE} />
        <Bar dataKey="t0" name="T-0 (atual)" fill={CHART_COLORS.primary} radius={[4, 4, 0, 0]} />
        <Bar dataKey="t1" name="T-1 (anterior)" fill={CHART_COLORS.compare} radius={[4, 4, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}

export function TotalVolumeTrendChart({ totalVolumeTrendChartData }: { totalVolumeTrendChartData: ChartRows }) {
  return (
    <ResponsiveContainer width="100%" height={280}>
      <LineChart data={totalVolumeTrendChartData} margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
        <CartesianGrid stroke={CHART_GRID_STROKE} strokeDasharray="4 4" vertical={false} />
        <XAxis dataKey="periodo" tick={CHART_AXIS_TICK} />
        <YAxis tick={CHART_AXIS_TICK} width={48} />
        <Tooltip
          contentStyle={CHART_TOOLTIP_CONTENT_STYLE}
          labelStyle={CHART_TOOLTIP_LABEL_STYLE}
          itemStyle={CHART_TOOLTIP_ITEM_STYLE}
          formatter={(value) => [formatKgFixed(Number(value), 0), 'Volume total']}
        />
        <Line
          type="monotone"
          dataKey="volume"
          name="Volume total"
          stroke={CHART_COLORS.primary}
          strokeWidth={2}
          dot={{ r: 4, fill: CHART_COLORS.primary }}
          activeDot={{ r: 6 }}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}

export function RepsChart({ repsChartData, currentLabel, previousLabel }: { repsChartData: ChartRows; currentLabel: string; previousLabel: string }) {
  return (
    <ResponsiveContainer width="100%" height={320}>
      <BarChart data={repsChartData} margin={{ top: 8, right: 16, left: 0, bottom: 48 }}>
        <CartesianGrid stroke={CHART_GRID_STROKE} strokeDasharray="4 4" vertical={false} />
        <XAxis dataKey="grupo" tick={CHART_AXIS_TICK} interval={0} angle={-30} textAnchor="end" height={70} />
        <YAxis tick={CHART_AXIS_TICK} width={48} />
        <Tooltip
          contentStyle={CHART_TOOLTIP_CONTENT_STYLE}
          labelStyle={CHART_TOOLTIP_LABEL_STYLE}
          itemStyle={CHART_TOOLTIP_ITEM_STYLE}
          formatter={(value) => `${formatFixed(Number(value), 0)} reps`}
        />
        <Legend wrapperStyle={CHART_LEGEND_STYLE} />
        <Bar dataKey="atual" name={currentLabel} fill={CHART_COLORS.primary} radius={[4, 4, 0, 0]} />
        <Bar dataKey="anterior" name={previousLabel} fill={CHART_COLORS.compare} radius={[4, 4, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}

export function HeatmapTreemap({ heatmapTreemapData }: { heatmapTreemapData: ChartRows }) {
  return (
    <ResponsiveContainer width="100%" height={360}>
      <Treemap
        data={heatmapTreemapData}
        dataKey="sets_count"
        nameKey="name"
        stroke={BRAND.bg}
        content={<HeatmapCellContent />}
        isAnimationActive={false}
      >
        <Tooltip content={<HeatmapTooltip />} />
      </Treemap>
    </ResponsiveContainer>
  );
}
