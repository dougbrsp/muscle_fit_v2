'use client';

import { Badge } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { Suspense, useMemo, useState } from 'react';
import { CartesianGrid, ComposedChart, Line, ReferenceLine, Scatter, Tooltip, XAxis, YAxis } from 'recharts';
import {
  CHART_COLORS,
  CHART_GRID_STROKE,
  CHART_TOOLTIP_CONTENT_STYLE,
  CHART_TOOLTIP_ITEM_STYLE,
  CHART_TOOLTIP_LABEL_STYLE,
} from '@/components/charts/chartTheme';
import { StatusBadge } from '@/components/dashboard/StatusBadge';
import { dashboardKeys, fetchBody } from '@/lib/dashboard-api';
import {
  checkVisual,
  collectBodyLimitations,
  dateToTime,
  measurementChartRows,
  milestonesInRange,
  performanceChartRows,
  RECOMPOSITION_VISUAL,
  sharedTimeDomain,
  weightChartRows,
} from '@/lib/dashboard-body';
import { MEASUREMENT_SITE_LABELS, WAIST_SITE } from '@/lib/body-measurement-sites';
import type { BodyMeasurementSeries, BodyResponse } from '@/lib/dashboard-body-types';
import { useDashboardFilters } from '@/lib/dashboard-filters';
import { fmtDate, fmtDecimal } from '@/lib/dashboard-format';
import { MOBILE_TOKENS } from '../../tokens';
import {
  EmptyState,
  ListBlock,
  ListGroup,
  ListRow,
  MOBILE_CHART_AXIS,
  MOBILE_CHART_MARGIN,
  MobileChart,
  ScreenHeader,
} from '../../ui';
import styles from './evolucao.module.css';
import { plural } from './pure';
import { BlockSkeleton, LimitationsNote, metricLimitations, metricText, PartialNote, PeriodFilter, QueryGate } from './shared';

export function CorpoScreen() {
  return (
    <Suspense fallback={<BlockSkeleton rows={4} label="Carregando corpo" />}>
      <CorpoContent />
    </Suspense>
  );
}

const tickDate = (v: number) => fmtDate(new Date(v).toISOString().slice(0, 10));
const TOOLTIP = {
  contentStyle: CHART_TOOLTIP_CONTENT_STYLE,
  labelStyle: CHART_TOOLTIP_LABEL_STYLE,
  itemStyle: CHART_TOOLTIP_ITEM_STYLE,
};

function CorpoContent() {
  const { filters, setFilters, windowQuery, rangeError } = useDashboardFilters();
  const query = useQuery({ queryKey: dashboardKeys.body(windowQuery), queryFn: () => fetchBody(windowQuery) });
  return (
    <>
      <ScreenHeader title="Corpo" subtitle="Peso, medidas, bioimpedância estimada e sinal de recomposição." />
      <PeriodFilter filters={filters} setFilters={setFilters} rangeError={rangeError} />
      <QueryGate query={query} title="corpo" rows={5}>{(p) => <CorpoBody payload={p} />}</QueryGate>
    </>
  );
}

function CorpoBody({ payload }: { payload: BodyResponse }) {
  const limitacoes = useMemo(() => collectBodyLimitations(payload), [payload]);
  const dominio = useMemo(() => sharedTimeDomain(payload.window.date_from, payload.window.date_to), [payload]);
  const marcos = useMemo(
    () => milestonesInRange(payload.milestones, payload.window.date_from, payload.window.date_to),
    [payload],
  );
  const sinal = payload.recomposition;
  const visual = RECOMPOSITION_VISUAL[sinal.status];
  const peso = payload.weight;
  const pesoRows = useMemo(() => weightChartRows(peso.points), [peso.points]);
  const desempenho = payload.performance;
  const desempRows = useMemo(() => performanceChartRows(desempenho.points), [desempenho.points]);
  const cintura = payload.waist;
  const cinturaRows = useMemo(() => measurementChartRows(cintura.points), [cintura.points]);
  const bio = payload.bioimpedance;

  return (
    <>
      <ListGroup
        title="Sinal de recomposição"
        footer={`Janela própria de ${sinal.window.days} dias (${fmtDate(sinal.window.date_from)} a ${fmtDate(sinal.window.date_to)}). Inferido, nunca uma prova.`}
      >
        <ListBlock>
          <div className={styles.badges}>
            <Badge color={visual.color} variant="light" size="lg">{visual.label}</Badge>
            <StatusBadge domain="dataNature" code={sinal.nature} size="sm" />
            <StatusBadge domain="dataQuality" code={sinal.data_quality} size="sm" />
          </div>
          <p className={styles.meta} style={{ marginTop: 0 }}>{visual.description}</p>
          <p className={styles.body} style={{ marginTop: 8 }}>{sinal.answer}</p>
        </ListBlock>
        {sinal.checks.map((c) => {
          const cv = checkVisual(c.result);
          return (
            <ListRow
              key={c.code}
              title={c.label}
              subtitle={c.detail}
              trailing={<Badge color={cv.color} variant="light" size="sm">{cv.label}</Badge>}
            />
          );
        })}
        {sinal.evidence.length > 0 ? (
          <ListBlock>
            <p className={styles.meta} style={{ marginTop: 0 }}>
              Evidências:{' '}
              {sinal.evidence
                .map((e) =>
                  e.from_value != null
                    ? `${e.metric} ${fmtDecimal(e.from_value, 1)} → ${fmtDecimal(e.to_value, 1)} ${e.unit ?? ''}`
                    : `${e.metric} ${fmtDecimal(e.to_value, 1)} ${e.unit ?? ''}`,
                )
                .join(' · ')}
            </p>
          </ListBlock>
        ) : null}
      </ListGroup>
      <PartialNote quality={sinal.data_quality} limitations={sinal.limitations} title="Limitações da recomposição" fixHref={null} />
      {sinal.alternative_explanations.length > 0 ? (
        <ListGroup title="Explicações alternativas">
          <ListBlock>
            {sinal.alternative_explanations.map((a) => (
              <p key={a} className={styles.meta} style={{ marginTop: 0 }}>• {a}</p>
            ))}
          </ListBlock>
        </ListGroup>
      ) : null}
      <PartialNote quality={payload.data_quality} limitations={limitacoes} />

      <ListGroup title="Peso" footer={peso.answer}>
        <ListRow title="Última pesagem" value={metricText(peso.latest)} />
        <ListRow title="Média de 7 dias" value={metricText(peso.ma7_latest)} />
        <ListRow title="Variação da média no período" value={`${metricText(peso.change)} · ${metricText(peso.change_pct)}`} />
        <ListRow title="Pesagens por semana" value={metricText(peso.weighings_per_week)} />
        {pesoRows.length === 0 ? (
          <ListBlock><p className={styles.body}>Nenhuma pesagem no período. Dias sem pesagem não viram zero.</p></ListBlock>
        ) : (
          <div className={styles.chartBlock}>
            <MobileChart summary={peso.answer} label="Peso e média de 7 dias">
              <ComposedChart data={pesoRows} margin={MOBILE_CHART_MARGIN}>
                <CartesianGrid stroke={CHART_GRID_STROKE} strokeDasharray="3 3" />
                <XAxis dataKey="t" type="number" domain={dominio} tickFormatter={tickDate} {...MOBILE_CHART_AXIS} />
                <YAxis domain={['auto', 'auto']} {...MOBILE_CHART_AXIS} width={44} />
                <Tooltip
                  {...TOOLTIP}
                  labelFormatter={(v) => tickDate(Number(v))}
                  formatter={(value, name, item) => {
                    const row = item.payload as (typeof pesoRows)[number];
                    return name === 'Média 7 dias'
                      ? [`${fmtDecimal(Number(value), 1)} kg (${row.pontos} pesagens)`, name]
                      : [`${fmtDecimal(Number(value), 1)} kg · ${row.fonte}`, name];
                  }}
                />
                <Line type="monotone" dataKey="peso" name="Pesagem" stroke={CHART_COLORS.compare} strokeWidth={1} dot={{ r: 2 }} />
                <Line type="monotone" dataKey="media7" name="Média 7 dias" stroke={MOBILE_TOKENS.accent} strokeWidth={2} dot={false} connectNulls />
                {marcos.map((m) => (
                  <ReferenceLine key={`${m.kind}-${m.local_date}`} x={dateToTime(m.local_date)} stroke={CHART_COLORS.secondary} strokeDasharray="4 4" />
                ))}
              </ComposedChart>
            </MobileChart>
          </div>
        )}
      </ListGroup>
      <LimitationsNote items={metricLimitations([peso.latest, peso.ma7_latest, peso.change, peso.change_pct, peso.weighings_per_week])} />
      <PartialNote quality={peso.data_quality} limitations={peso.limitations} fixHref={null} />

      <ListGroup
        title="Desempenho (e1RM médio dos comparáveis)"
        footer="Só entram as execuções comparáveis de cada exercício (mesmo equipamento e convenção de carga) e o 1RM estimado de séries com até 12 repetições. Mesmo eixo de tempo do peso; eixo próprio."
      >
        <ListBlock>
          <p className={styles.body}>
            {desempenho.progressing_or_stable} de {desempenho.comparable_exercises} exercícios comparáveis progredindo ou estáveis.
          </p>
        </ListBlock>
        {desempRows.length === 0 ? (
          <ListBlock><p className={styles.body}>Nenhum exercício com histórico comparável no período.</p></ListBlock>
        ) : (
          <div className={styles.chartBlock}>
            <MobileChart summary="e1RM médio estimado dos exercícios comparáveis." label="e1RM médio">
              <ComposedChart data={desempRows} margin={MOBILE_CHART_MARGIN}>
                <CartesianGrid stroke={CHART_GRID_STROKE} strokeDasharray="3 3" />
                <XAxis dataKey="t" type="number" domain={dominio} tickFormatter={tickDate} {...MOBILE_CHART_AXIS} />
                <YAxis domain={['auto', 'auto']} {...MOBILE_CHART_AXIS} width={44} />
                <Tooltip
                  {...TOOLTIP}
                  labelFormatter={(v) => tickDate(Number(v))}
                  formatter={(value, name, item) => [
                    `${fmtDecimal(Number(value), 1)} kg (${(item.payload as (typeof desempRows)[number]).exercicios} exercícios) · estimado`,
                    name,
                  ]}
                />
                <Line type="monotone" dataKey="e1rm" name="e1RM médio" stroke={MOBILE_TOKENS.accent} strokeWidth={2} dot={{ r: 2 }} />
              </ComposedChart>
            </MobileChart>
          </div>
        )}
      </ListGroup>
      <PartialNote quality={desempenho.data_quality} limitations={desempenho.limitations} fixHref={null} />

      <ListGroup
        title={MEASUREMENT_SITE_LABELS[WAIST_SITE]}
        footer="Última medida NO PROTOCOLO e a variação entre a primeira e a última no protocolo. Medidas fora do protocolo aparecem como pontos e não entram no sinal."
      >
        <ListRow
          title="Última (no protocolo)"
          subtitle={cintura.latest_local_date ? fmtDate(cintura.latest_local_date) : (cintura.limitations[0] ?? 'Sem medida.')}
          value={metricText(cintura.latest)}
        />
        <ListRow
          title="Variação"
          subtitle={cintura.change_span_days ? `em ${cintura.change_span_days} dias` : (cintura.limitations[0] ?? undefined)}
          value={metricText(cintura.change)}
        />
        <ListRow title="Medidas (protocolo / fora)" value={`${cintura.protocol_points} / ${cintura.out_of_protocol_points}`} />
        {cinturaRows.length === 0 ? (
          <ListBlock>
            <p className={styles.body}>
              {cintura.table_available ? 'Nenhuma medida de cintura no período.' : 'Medidas indisponíveis.'} {cintura.limitations[0] ?? ''}
            </p>
          </ListBlock>
        ) : (
          <MedidaGrafico rows={cinturaRows} dominio={dominio} label="Cintura" />
        )}
      </ListGroup>
      <PartialNote quality={cintura.data_quality} limitations={cintura.available ? cintura.limitations : []} fixHref={null} />

      <OutrasMedidas series={payload.measurements} dominio={dominio} />

      <ListGroup
        title="Bioimpedância"
        footer={`Estimativa da balança${bio.latest_local_date ? ` · última leitura ${fmtDate(bio.latest_local_date)}` : ''} · ${plural(bio.readings_count, 'leitura', 'leituras')} no período.`}
      >
        <ListBlock>
          <div className={styles.badges} style={{ margin: 0 }}>
            <StatusBadge domain="dataNature" code="ESTIMATED" size="sm" />
            <StatusBadge domain="dataQuality" code={bio.data_quality} size="sm" />
          </div>
        </ListBlock>
        <ListRow title="Gordura corporal" value={metricText(bio.body_fat_percent)} />
        <ListRow title="Massa muscular" value={metricText(bio.muscle_mass_kg)} />
        <ListRow title="Água corporal" value={metricText(bio.body_water_percent)} />
        <ListRow title="Gordura: variação" value={metricText(bio.body_fat_change)} subtitle={bio.body_fat_change.value == null ? 'Uma leitura isolada não confirma tendência.' : undefined} />
        <ListRow title="Massa muscular: variação" value={metricText(bio.muscle_mass_change)} subtitle={bio.muscle_mass_change.value == null ? 'Uma leitura isolada não confirma tendência.' : undefined} />
      </ListGroup>
      <PartialNote quality={bio.data_quality} limitations={bio.limitations} fixHref={null} />

      <ListGroup title="Marcos e mudanças de fase" footer="Linhas tracejadas no gráfico de peso.">
        {marcos.length === 0 ? (
          <ListBlock><p className={styles.body}>Nenhum marco no período.</p></ListBlock>
        ) : (
          marcos.map((m) => (
            <ListRow key={`${m.kind}-${m.local_date}-${m.label}`} title={m.label} subtitle={m.detail ?? undefined} value={fmtDate(m.local_date)} />
          ))
        )}
      </ListGroup>
      <PartialNote quality={null} limitations={payload.milestones_limitations} title="Limitações" fixHref={null} />

      <ListGroup footer="Peso, bioimpedância e medidas por fita são registrados em Indicadores físicos, que também guarda a série histórica completa.">
        <ListRow title="Abrir Indicadores físicos" href="/indicadores-fisicos" />
      </ListGroup>
    </>
  );
}

function MedidaGrafico({
  rows,
  dominio,
  label,
}: {
  rows: ReturnType<typeof measurementChartRows>;
  dominio: [number, number];
  label: string;
}) {
  return (
    <div className={styles.chartBlock}>
      <MobileChart summary="Linha: medidas no protocolo. Pontos soltos: fora do protocolo (confiança baixa)." label={label}>
        <ComposedChart data={rows} margin={MOBILE_CHART_MARGIN}>
          <CartesianGrid stroke={CHART_GRID_STROKE} strokeDasharray="3 3" />
          <XAxis dataKey="t" type="number" domain={dominio} tickFormatter={tickDate} {...MOBILE_CHART_AXIS} />
          <YAxis domain={['auto', 'auto']} {...MOBILE_CHART_AXIS} width={44} />
          <Tooltip
            {...TOOLTIP}
            labelFormatter={(v) => tickDate(Number(v))}
            formatter={(value, name, item) => {
              const row = item.payload as (typeof rows)[number];
              return [`${fmtDecimal(Number(value), 1)} cm${row.notas ? ` · ${row.notas}` : ''}`, name];
            }}
          />
          <Line type="monotone" dataKey="protocolo" name="No protocolo" stroke={MOBILE_TOKENS.accent} strokeWidth={2} dot={{ r: 3 }} connectNulls />
          <Scatter dataKey="foraProtocolo" name="Fora do protocolo (LOW)" fill={CHART_COLORS.compare} />
        </ComposedChart>
      </MobileChart>
    </div>
  );
}

function OutrasMedidas({ series, dominio }: { series: BodyMeasurementSeries[]; dominio: [number, number] }) {
  const [site, setSite] = useState<string | null>(null);
  const escolhida = series.find((s) => s.site === site) ?? series[0];
  const rows = useMemo(() => (escolhida ? measurementChartRows(escolhida.points) : []), [escolhida]);
  if (series.length === 0) {
    return (
      <EmptyState
        title="Sem outras medidas"
        description="Nenhuma outra medida por fita no período. Registre em Indicadores físicos."
      />
    );
  }
  return (
    <ListGroup title="Outras medidas" footer="Demais locais medidos por fita, séries simples por local.">
      <ListBlock>
        <label className={styles.fieldLabel} htmlFor="mob-medida-local">Local</label>
        <select
          id="mob-medida-local"
          className={styles.dateInput}
          value={escolhida.site}
          onChange={(e) => setSite(e.currentTarget.value)}
        >
          {series.map((s) => (
            <option key={s.site} value={s.site}>{s.label}</option>
          ))}
        </select>
      </ListBlock>
      <ListRow title={`Última (${fmtDate(escolhida.latest_local_date)})`} value={metricText(escolhida.latest)} />
      <ListRow title="Variação no período" value={metricText(escolhida.change)} />
      <MedidaGrafico rows={rows} dominio={dominio} label={escolhida.label} />
    </ListGroup>
  );
}
