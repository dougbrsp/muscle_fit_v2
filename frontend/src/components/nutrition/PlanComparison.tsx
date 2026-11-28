'use client';

import { Alert, Badge, Card, SegmentedControl, Stack, Table, Text, Title } from '@mantine/core';
import { IconAlertTriangle } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip as ReTooltip, XAxis, YAxis } from 'recharts';
import {
  CHART_AXIS_TICK,
  CHART_COLORS,
  CHART_GRID_STROKE,
  CHART_LEGEND_STYLE,
  CHART_TOOLTIP_CONTENT_STYLE,
  CHART_TOOLTIP_ITEM_STYLE,
  CHART_TOOLTIP_LABEL_STYLE,
} from '@/components/charts/chartTheme';
import { formatNumberOrDash } from '@/lib/format';
import { planComparisonQuery } from '@/lib/nutrition-api';
import { DAY_KIND_OPTIONS, nutritionLabel } from '@/lib/nutrition-constants';
import { weekdayShort } from '@/lib/nutrition-format';
import type { NutritionComparedValue, NutritionComparisonContext, NutritionComparisonDay } from '@/lib/nutrition-types';
import { formatDateBR } from '@/lib/physical-indicators-types';
import { BRAND } from '@/theme';

// Rascunho × vigente (#344): só exibe valores, diferenças e percentuais prontos da API
// (`domain/nutrition/plan_comparison`); nenhum cálculo aqui.

type Metric = keyof NutritionComparisonDay['values'];

const METRICS: ReadonlyArray<{ key: Metric; label: string; unit: string; digits: number }> = [
  { key: 'kcal', label: 'Calorias', unit: 'kcal', digits: 0 },
  { key: 'protein_g', label: 'Proteína', unit: 'g', digits: 1 },
  { key: 'carbs_g', label: 'Carboidrato', unit: 'g', digits: 1 },
  { key: 'fat_g', label: 'Gordura', unit: 'g', digits: 1 },
];

const REASONS: Record<string, string> = {
  current_failed: 'Não foi possível calcular a prévia do plano vigente',
  draft_failed: 'Não foi possível calcular a prévia do rascunho',
  current_blocking: 'O plano vigente tem bloqueios do motor',
  draft_blocking: 'O rascunho tem bloqueios do motor (veja abaixo)',
  reference_date_mismatch: 'As duas prévias usaram datas de referência diferentes',
};
const ERROR_DETAILS: Record<string, string> = { nutrition_subject_incomplete: 'faltam dados do perfil' };

export function reasonText(reason: string): string {
  const [codigo, detalhe] = reason.split(':', 2);
  const base = REASONS[codigo] ?? reason;
  if (!detalhe || !REASONS[codigo]) return base;
  return `${base} (${ERROR_DETAILS[detalhe] ?? `código ${detalhe}`})`;
}

const HATCH_ID = 'hachura-rascunho';
const LABEL_CURRENT = 'Vigente';
const LABEL_DRAFT = 'Rascunho (hachurado)';

function signed(value: number | null, digits: number): string {
  const texto = formatNumberOrDash(value, digits);
  return value != null && value > 0 ? `+${texto}` : texto;
}

function amount(value: number | null, metric: (typeof METRICS)[number]): string {
  return value == null ? '-' : `${formatNumberOrDash(value, metric.digits)} ${metric.unit}`;
}

export function diffText(value: NutritionComparedValue, metric: (typeof METRICS)[number]): string {
  if (value.diff == null) return '-';
  const pct = value.diff_percent == null ? '' : ` (${signed(value.diff_percent, 1)}%)`;
  return `${signed(value.diff, metric.digits)} ${metric.unit}${pct}`;
}

function dayKind(ctx: NutritionComparisonContext): string {
  const tipo = nutritionLabel(DAY_KIND_OPTIONS, ctx.day_kind) ?? '-';
  return ctx.is_training_day ? `${tipo} · treino` : tipo;
}

function dayLabel(iso: string | null): string {
  return iso ? `${weekdayShort(iso)} ${formatDateBR(iso).slice(0, 5)}` : '-';
}

function TodayTable({ day }: { day: NutritionComparisonDay }) {
  return (
    <Table.ScrollContainer minWidth={420}>
      <Table data-testid="comparacao-hoje" striped>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Meta de hoje</Table.Th>
            <Table.Th ta="right">{LABEL_CURRENT}</Table.Th>
            <Table.Th ta="right">Rascunho</Table.Th>
            <Table.Th ta="right">Diferença</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {METRICS.map((m) => (
            <Table.Tr key={m.key} data-testid={`comparacao-hoje-${m.key}`}>
              <Table.Td>{m.label}</Table.Td>
              <Table.Td ta="right">{amount(day.values[m.key].current, m)}</Table.Td>
              <Table.Td ta="right">{amount(day.values[m.key].draft, m)}</Table.Td>
              <Table.Td ta="right">{diffText(day.values[m.key], m)}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}

function WeekChart({ days, metric }: { days: NutritionComparisonDay[]; metric: (typeof METRICS)[number] }) {
  const rows = days.map((d) => ({ label: dayLabel(d.local_date), vigente: d.values[metric.key].current, rascunho: d.values[metric.key].draft }));
  return (
    <div role="img" aria-label={`Gráfico de barras de ${metric.label} por dia: ${LABEL_CURRENT} e Rascunho. Os mesmos números estão na tabela abaixo.`}>
      <ResponsiveContainer width="100%" height={220}>
        <BarChart data={rows} margin={{ top: 8, right: 8, left: 0, bottom: 4 }} barGap={2}>
          <defs>
            <pattern id={HATCH_ID} width={6} height={6} patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
              <rect width={6} height={6} fill={BRAND.bgSurface} />
              <line x1={0} y1={0} x2={0} y2={6} stroke={CHART_COLORS.primary} strokeWidth={3} />
            </pattern>
          </defs>
          <CartesianGrid stroke={CHART_GRID_STROKE} strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="label" tick={CHART_AXIS_TICK} interval={0} />
          <YAxis tick={CHART_AXIS_TICK} width={44} />
          <ReTooltip
            contentStyle={CHART_TOOLTIP_CONTENT_STYLE}
            labelStyle={CHART_TOOLTIP_LABEL_STYLE}
            itemStyle={CHART_TOOLTIP_ITEM_STYLE}
            formatter={(value, name) => [amount(value == null ? null : Number(value), metric), String(name)]}
          />
          <Legend wrapperStyle={CHART_LEGEND_STYLE} />
          <Bar dataKey="vigente" name={LABEL_CURRENT} fill={CHART_COLORS.compare} radius={[4, 4, 0, 0]} maxBarSize={22} isAnimationActive={false} />
          <Bar
            dataKey="rascunho"
            name={LABEL_DRAFT}
            fill={`url(#${HATCH_ID})`}
            stroke={CHART_COLORS.primary}
            strokeWidth={1.5}
            radius={[4, 4, 0, 0]}
            maxBarSize={22}
            isAnimationActive={false}
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function WeekTable({ days, metric }: { days: NutritionComparisonDay[]; metric: (typeof METRICS)[number] }) {
  return (
    <Table.ScrollContainer minWidth={620}>
      <Table data-testid="comparacao-semana">
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Data</Table.Th>
            <Table.Th>Tipo de dia ({LABEL_CURRENT})</Table.Th>
            <Table.Th>Tipo de dia (Rascunho)</Table.Th>
            <Table.Th ta="right">{metric.label} ({LABEL_CURRENT})</Table.Th>
            <Table.Th ta="right">{metric.label} (Rascunho)</Table.Th>
            <Table.Th ta="right">Diferença</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {days.map((d) => (
            <Table.Tr
              key={d.local_date ?? ''}
              data-testid="comparacao-dia"
              data-differs={d.differs ? 'true' : 'false'}
              style={d.differs ? { background: 'var(--mantine-color-yellow-light)' } : undefined}
            >
              <Table.Td>
                {dayLabel(d.local_date)}{' '}
                {d.differs && (
                  <Badge size="xs" color="yellow" variant="light">
                    Muda
                  </Badge>
                )}
              </Table.Td>
              <Table.Td>{dayKind(d.current_context)}</Table.Td>
              <Table.Td>{dayKind(d.draft_context)}</Table.Td>
              <Table.Td ta="right">{amount(d.values[metric.key].current, metric)}</Table.Td>
              <Table.Td ta="right">{amount(d.values[metric.key].draft, metric)}</Table.Td>
              <Table.Td ta="right">{diffText(d.values[metric.key], metric)}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Table.ScrollContainer>
  );
}

type Props = {
  plan: { id: number; updated_at: string | null };
  current: { id: number; updated_at: string | null };
};

/** Bloco "Rascunho × Vigente" da prévia: aviso nos estados sem comparação, nunca esconde a prévia. */
export function PlanComparison({ plan, current }: Props) {
  const query = useQuery(planComparisonQuery(plan, current));
  const [metricKey, setMetricKey] = useState<Metric>('kcal');
  const metric = METRICS.find((m) => m.key === metricKey) ?? METRICS[0];

  if (query.isLoading) return <Text size="sm" c={BRAND.textMuted}>Comparando com o plano vigente...</Text>;
  if (query.isError || !query.data) {
    return (
      <Alert color="yellow" icon={<IconAlertTriangle size={16} />} data-testid="comparacao-erro">
        Não foi possível comparar com o plano vigente agora. A prévia abaixo continua válida.
      </Alert>
    );
  }
  const { status, reasons, comparison } = query.data;
  if (status === 'same_plan') return null;
  if (status === 'no_current') {
    return (
      <Text size="sm" c="dimmed" data-testid="comparacao-sem-vigente">
        Sem plano vigente para comparar
      </Text>
    );
  }

  return (
    <Card withBorder radius="md" bg={BRAND.bgSurface} data-testid="comparacao-plano">
      <Stack gap="sm">
        <Title order={4} c={BRAND.textPrimary}>
          Comparação com o plano vigente
        </Title>
        {status === 'partial' && (
          <Alert color="yellow" icon={<IconAlertTriangle size={16} />} data-testid="comparacao-parcial" title="Comparação parcial">
            <Stack gap={2}>
              {reasons.map((r) => (
                <Text key={r} size="sm">
                  {reasonText(r)}
                </Text>
              ))}
            </Stack>
          </Alert>
        )}
        {comparison && (
          <>
            <TodayTable day={comparison.target} />
            <Text size="sm" fw={600} c={BRAND.textPrimary}>
              Semana por data
            </Text>
            <SegmentedControl
              aria-label="Valor comparado na semana"
              value={metricKey}
              onChange={(v) => setMetricKey(v as Metric)}
              data={METRICS.map((m) => ({ value: m.key, label: m.label }))}
              fullWidth
              size="xs"
            />
            <WeekChart days={comparison.days} metric={metric} />
            <WeekTable days={comparison.days} metric={metric} />
            {comparison.only_current.length > 0 && (
              <Text size="xs" c="dimmed">
                Só no vigente: {comparison.only_current.map(dayLabel).join(', ')}
              </Text>
            )}
            {comparison.only_draft.length > 0 && (
              <Text size="xs" c="dimmed">
                Só no rascunho: {comparison.only_draft.map(dayLabel).join(', ')}
              </Text>
            )}
          </>
        )}
      </Stack>
    </Card>
  );
}
