'use client';

import { Badge } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { Suspense, useMemo } from 'react';
import { Bar, BarChart, Legend, Line, LineChart, ReferenceLine, Tooltip, XAxis, YAxis } from 'recharts';
import {
  CHART_COLORS,
  CHART_TOOLTIP_CONTENT_STYLE,
  CHART_TOOLTIP_ITEM_STYLE,
  CHART_TOOLTIP_LABEL_STYLE,
} from '@/components/charts/chartTheme';
import { StatusBadge } from '@/components/dashboard/StatusBadge';
import { dashboardKeys, fetchCardio } from '@/lib/dashboard-api';
import {
  adherenceStatusColor,
  collectCardioLimitations,
  groupSessionsByDay,
  hasAnythingToShow,
  hasZoneData,
  matchBadge,
  matchDeltaLabel,
  modeChartSummary,
  paiRows,
  sportLabel,
  weeklyModeRows,
  weeklyZoneRows,
  wellnessRows,
  zoneChartSummary,
  zoneSourceLabel,
  ZONE_KEYS,
} from '@/lib/dashboard-cardio';
import type { CardioResponse } from '@/lib/dashboard-cardio-types';
import { useDashboardFilters } from '@/lib/dashboard-filters';
import { EM_DASH, fmtDate, fmtDateTime, fmtDecimal, fmtInt, fmtPercent } from '@/lib/dashboard-format';
import { isDesktopOnlyPath } from '@/lib/nav-items';
import { BRAND, WORKOUT_MODE_TOKENS } from '@/theme';
import {
  EmptyState,
  ListBlock,
  ListGroup,
  ListRow,
  MOBILE_CHART_AXIS,
  MOBILE_CHART_MARGIN,
  MobileChart,
  ScreenHeader,
  StatGrid,
  StatTile,
} from '../../ui';
import styles from './evolucao.module.css';
import { plural } from './pure';
import { BlockSkeleton, PartialNote, PeriodFilter, QueryGate, SheetRow } from './shared';
import { useIsAdmin } from '@/lib/use-is-admin';

const STALE_TIME = 60_000;
const ZONE_COLORS: Record<(typeof ZONE_KEYS)[number], string> = {
  Z1: CHART_COLORS.compare,
  Z2: CHART_COLORS.positive,
  Z3: WORKOUT_MODE_TOKENS.warning,
  Z4: BRAND.orange,
  Z5: BRAND.red,
};
const TOOLTIP = {
  contentStyle: CHART_TOOLTIP_CONTENT_STYLE,
  labelStyle: CHART_TOOLTIP_LABEL_STYLE,
  itemStyle: CHART_TOOLTIP_ITEM_STYLE,
};
const num = (v: number | null, unit?: string) =>
  v == null ? EM_DASH : `${fmtDecimal(v, Number.isInteger(v) ? 0 : 1)}${unit ? ` ${unit}` : ''}`;
const min = (v: unknown) => `${fmtInt(Number(v))} min`;

export function CardioScreen() {
  return (
    <Suspense fallback={<BlockSkeleton rows={4} label="Carregando cardio" />}>
      <CardioContent />
    </Suspense>
  );
}

function CardioContent() {
  const { filters, setFilters, windowQuery, rangeError } = useDashboardFilters();
  const query = useQuery({
    queryKey: dashboardKeys.cardio(windowQuery),
    queryFn: () => fetchCardio(windowQuery),
    staleTime: STALE_TIME,
  });
  return (
    <>
      <ScreenHeader title="Cardio" subtitle="Cardio sincronizado do Intervals.icu e FC de cada sessão." />
      <PeriodFilter filters={filters} setFilters={setFilters} rangeError={rangeError} />
      <QueryGate query={query} title="cardio" rows={5}>{(p) => <CardioBody payload={p} />}</QueryGate>
    </>
  );
}

function SmallLine({
  data,
  dataKey,
  name,
  unit,
  summary,
  color,
}: {
  data: ReturnType<typeof wellnessRows>;
  dataKey: 'hrv' | 'fcRepouso';
  name: string;
  unit: string;
  summary: string;
  color: string;
}) {
  return (
    <div className={styles.chartBlock}>
      <MobileChart summary={summary} label={name}>
        <LineChart data={data} margin={MOBILE_CHART_MARGIN}>
          <XAxis dataKey="data" {...MOBILE_CHART_AXIS} />
          <YAxis {...MOBILE_CHART_AXIS} width={40} domain={['auto', 'auto']} />
          <Tooltip {...TOOLTIP} formatter={(v) => [`${fmtDecimal(Number(v), 0)} ${unit}`, name]} />
          <Line type="monotone" dataKey={dataKey} name={name} stroke={color} strokeWidth={2} dot={{ r: 2 }} connectNulls />
        </LineChart>
      </MobileChart>
    </div>
  );
}

function CardioBody({ payload }: { payload: CardioResponse }) {
  const isAdmin = useIsAdmin();
  const limitacoes = useMemo(() => collectCardioLimitations(payload), [payload]);
  const { totals } = payload;
  const zonas = useMemo(() => weeklyZoneRows(payload.weeks), [payload.weeks]);
  const modos = useMemo(() => weeklyModeRows(payload.weeks), [payload.weeks]);
  const wellness = useMemo(() => wellnessRows(payload.wellness?.points ?? []), [payload.wellness]);
  const pai = payload.pai;
  const paiLinhas = useMemo(() => (pai ? paiRows(pai) : []), [pai]);
  const dias = useMemo(() => groupSessionsByDay(payload.sessions), [payload.sessions]);
  const next = payload.next_session;
  const bloco = payload.strength_heart_rate;
  const latest = payload.wellness?.latest;
  const agenteHref = isDesktopOnlyPath('/agente-cardio') ? null : '/agente-cardio';
  const temSemanas = (payload.weeks?.length ?? 0) > 0 && totals.sessions > 0;

  return (
    <>
      {!payload.intervals_configured ? (
        <ListGroup footer="A frequência cardíaca das sessões já associadas continua aparecendo abaixo.">
          <ListRow title="Intervals.icu não configurado" subtitle="O cardio do hub vem do Intervals.icu" value="Configurar" href="/intervals-icu" />
        </ListGroup>
      ) : null}

      <ListGroup title="Resumo do período">
        <ListBlock>
          <StatGrid>
            <StatTile label="Sessões de cardio" value={num(totals.sessions)} />
            <StatTile label="Minutos" value={num(totals.minutes)} />
            <StatTile label="Distância" value={num(totals.distance_km, 'km')} />
            <StatTile label="Calorias" value={num(totals.calories_kcal, 'kcal')} />
            <StatTile label="FC média" value={num(totals.avg_hr_bpm, 'bpm')} />
            <StatTile label="FC máxima" value={num(totals.max_hr_bpm, 'bpm')} />
          </StatGrid>
        </ListBlock>
        <ListBlock>
          <div className={styles.badges}>
            <StatusBadge domain="dataNature" code={payload.nature} size="sm" />
            {isAdmin && <StatusBadge domain="dataQuality" code={payload.data_quality} size="sm" />}
          </div>
          <p className={styles.meta} style={{ marginTop: 0 }}>
            {fmtDate(payload.window.date_from)} a {fmtDate(payload.window.date_to)} · carga (Intervals) {num(totals.training_load)} · sincronizado em{' '}
            {payload.last_synced_at ? fmtDateTime(payload.last_synced_at) : EM_DASH}. Vem do cache do sincronismo automático; atividade sem sensor de FC
            conta nos minutos e fica fora da média.
          </p>
          {payload.other_activities_total > 0 ? (
            <p className={styles.meta}>
              {fmtInt(payload.other_activities_total)} outra(s) atividade(s) (yoga, alongamento) ficam fora dos totais: nada vira cardio por eliminação.
            </p>
          ) : null}
        </ListBlock>
      </ListGroup>

      {next ? (
        <ListGroup title="Próxima sessão recomendada" footer={`Gerada em ${fmtDateTime(next.generated_at)}, pelo Agente de Cardio.`}>
          <ListBlock>
            <div className={styles.badges}>
              <Badge color="orange" variant="light" size="lg">{next.date_label ?? EM_DASH}</Badge>
              {next.total_minutes_label ? <Badge color="gray" variant="outline" size="lg">{next.total_minutes_label}</Badge> : null}
              {next.slot_name ? <Badge color="blue" variant="light" size="lg">{next.slot_name}</Badge> : null}
            </div>
            <p className={styles.headline}>{next.prescription_text ?? EM_DASH}</p>
            {next.rationale ? <p className={styles.meta}>{next.rationale}</p> : null}
            {next.adjustment_labels.length > 0 ? (
              <div className={styles.badges} style={{ marginTop: 8 }}>
                {next.adjustment_labels.map((r) => (
                  <Badge key={r} color="yellow" variant="light" size="sm">{r}</Badge>
                ))}
              </div>
            ) : null}
          </ListBlock>
          {agenteHref ? <ListRow title="Abrir o Agente de Cardio" href={agenteHref} /> : null}
        </ListGroup>
      ) : null}

      <PartialNote quality={payload.data_quality} limitations={limitacoes} />

      {temSemanas ? (
        <ListGroup
          title="Cardio por semana"
          footer="Semana de segunda a domingo. Minutos por zona do tempo em zona do Intervals.icu; a sessão intensa (HIIT) é classificada pelo motor."
        >
          {hasZoneData(payload.weeks) ? (
            <div className={styles.chartBlock}>
              <MobileChart summary={zoneChartSummary(payload.weeks)} label="Minutos por zona, por semana">
                <BarChart data={zonas} margin={MOBILE_CHART_MARGIN}>
                  <XAxis dataKey="semana" {...MOBILE_CHART_AXIS} />
                  <YAxis {...MOBILE_CHART_AXIS} width={36} />
                  <Tooltip {...TOOLTIP} labelFormatter={(l) => `Semana de ${l}`} formatter={(v, n) => [min(v), String(n)]} />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                  {ZONE_KEYS.map((z) => (
                    <Bar key={z} dataKey={z} name={z} stackId="zonas" fill={ZONE_COLORS[z]} />
                  ))}
                </BarChart>
              </MobileChart>
            </div>
          ) : (
            <ListBlock>
              <p className={styles.body}>Nenhuma atividade da janela trouxe tempo em zona: minutos por zona e classe contínuo × intensa não podem ser mostrados.</p>
            </ListBlock>
          )}
          <SheetRow title="Contínuo × intensa" subtitle="Minutos por tipo, por semana" sheetTitle="Contínuo × intensa">
            <div className={styles.chartBlock}>
              <MobileChart summary={modeChartSummary(payload.weeks)} label="Contínuo e intensa, por semana">
                <BarChart data={modos} margin={MOBILE_CHART_MARGIN}>
                  <XAxis dataKey="semana" {...MOBILE_CHART_AXIS} />
                  <YAxis {...MOBILE_CHART_AXIS} width={36} />
                  <Tooltip
                    {...TOOLTIP}
                    labelFormatter={(l) => `Semana de ${l}`}
                    formatter={(v, n, item) => {
                      const r = item.payload as (typeof modos)[number];
                      const s = n === 'Contínuo' ? r.sessoesContinuo : n === 'Intensa (HIIT)' ? r.sessoesIntensa : r.sessoesSemClasse;
                      return [`${min(v)} · ${plural(s, 'sessão', 'sessões')}`, String(n)];
                    }}
                  />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                  <Bar dataKey="continuo" name="Contínuo" stackId="modo" fill={CHART_COLORS.positive} />
                  <Bar dataKey="intensa" name="Intensa (HIIT)" stackId="modo" fill={CHART_COLORS.secondary} />
                  <Bar dataKey="semClasse" name="Sem tempo em zona" stackId="modo" fill={CHART_COLORS.compare} />
                </BarChart>
              </MobileChart>
            </div>
          </SheetRow>
          <SheetRow title="Todas as semanas" value={`${payload.weeks.length}`} sheetTitle="Semanas de cardio">
            <ListGroup>
              {payload.weeks.map((w) => (
                <ListRow
                  key={w.week_start}
                  title={`Semana de ${fmtDate(w.week_start)}`}
                  subtitle={`${plural(w.sessions, 'sessão', 'sessões')} · contínuo ${fmtInt(w.continuous_sessions)} (${fmtInt(w.continuous_minutes)} min) · intensa ${fmtInt(w.high_intensity_sessions)} (${fmtInt(w.high_intensity_minutes)} min) · sem zona ${fmtInt(w.unclassified_sessions)} (${fmtInt(w.unclassified_minutes)} min) · ${w.calories_kcal == null ? EM_DASH : fmtInt(w.calories_kcal)} kcal`}
                  value={`${fmtInt(w.minutes)} min`}
                />
              ))}
            </ListGroup>
          </SheetRow>
        </ListGroup>
      ) : null}

      {payload.wellness && payload.wellness.points.length > 0 && latest ? (
        <ListGroup
          title="Carga e recuperação"
          footer="Aptidão (CTL), fadiga (ATL) e forma (TSB) do Intervals.icu; HRV e FC de repouso como gravados no cache (inclusive a edição manual)."
        >
          <div className={styles.chartBlock}>
            <MobileChart
              summary={`CTL ${fmtDecimal(latest.ctl, 1)} · ATL ${fmtDecimal(latest.atl, 1)} · TSB ${fmtDecimal(latest.tsb, 1)} no último dia.`}
              label="Tendência de CTL, ATL e TSB"
            >
              <LineChart data={wellness} margin={MOBILE_CHART_MARGIN}>
                <XAxis dataKey="data" {...MOBILE_CHART_AXIS} />
                <YAxis {...MOBILE_CHART_AXIS} width={36} />
                <ReferenceLine y={0} stroke="rgba(255,255,255,0.2)" />
                <Tooltip {...TOOLTIP} formatter={(v, n) => [fmtDecimal(Number(v), 1), String(n)]} />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Line type="monotone" dataKey="ctl" name="Aptidão (CTL)" stroke={CHART_COLORS.primary} strokeWidth={2} dot={false} connectNulls />
                <Line type="monotone" dataKey="atl" name="Fadiga (ATL)" stroke={CHART_COLORS.secondary} strokeWidth={2} dot={false} connectNulls />
                <Line type="monotone" dataKey="tsb" name="Forma (TSB)" stroke={CHART_COLORS.positive} strokeWidth={2} strokeDasharray="5 3" dot={false} connectNulls />
              </LineChart>
            </MobileChart>
          </div>
          <SheetRow
            title="HRV (variabilidade da FC)"
            subtitle={latest.hrv_ms_date ? `Último registro em ${fmtDate(latest.hrv_ms_date)}` : 'Sem registro'}
            value={num(latest.hrv_ms, 'ms')}
            sheetTitle="HRV por dia"
          >
            <SmallLine data={wellness} dataKey="hrv" name="HRV" unit="ms" summary={`Último: ${num(latest.hrv_ms, 'ms')}`} color={CHART_COLORS.primary} />
          </SheetRow>
          <SheetRow
            title="FC de repouso"
            subtitle={latest.resting_hr_bpm_date ? `Último registro em ${fmtDate(latest.resting_hr_bpm_date)}` : 'Sem registro'}
            value={num(latest.resting_hr_bpm, 'bpm')}
            sheetTitle="FC de repouso por dia"
          >
            <SmallLine data={wellness} dataKey="fcRepouso" name="FC de repouso" unit="bpm" summary={`Último: ${num(latest.resting_hr_bpm, 'bpm')}`} color={CHART_COLORS.secondary} />
          </SheetRow>
        </ListGroup>
      ) : null}

      {pai && pai.points.length > 0 ? (
        <ListGroup title="PAI 7 dias (estimado)" footer="Acumulado de 7 dias estimado do tempo em zona de FC das atividades do Intervals.icu (não é a leitura do relógio); não depende do wellness.">
          <SheetRow
            title="PAI 7 dias (estimado)"
            subtitle={`${pai.latest_below_target == null ? '' : pai.latest_below_target ? 'Abaixo da meta' : 'Na meta'}${pai.latest_below_target == null ? '' : ' · '}meta ${fmtInt(pai.target_min)}${pai.latest_date ? ` · último em ${fmtDate(pai.latest_date)}` : ''}`}
            value={pai.latest == null ? EM_DASH : fmtInt(pai.latest)}
            sheetTitle="PAI de 7 dias (estimado)"
          >
            <div className={styles.chartBlock}>
              <MobileChart
                summary={`Acumulado de 7 dias estimado do tempo em zona de FC do Intervals.icu (não é a leitura do relógio). Linha tracejada: meta ${fmtInt(pai.target_min)}.`}
                label="PAI de 7 dias estimado"
              >
                <LineChart data={paiLinhas} margin={MOBILE_CHART_MARGIN}>
                  <XAxis dataKey="data" {...MOBILE_CHART_AXIS} />
                  <YAxis {...MOBILE_CHART_AXIS} width={36} domain={[0, 'auto']} />
                  <Tooltip {...TOOLTIP} formatter={(v, n) => [fmtInt(Number(v)), String(n)]} />
                  <ReferenceLine y={pai.target_min} stroke={CHART_COLORS.compare} strokeDasharray="4 4" />
                  <Line type="monotone" dataKey="pai" name="PAI 7 dias (estimado)" stroke={CHART_COLORS.primary} strokeWidth={2} dot={{ r: 2 }} />
                </LineChart>
              </MobileChart>
            </div>
          </SheetRow>
        </ListGroup>
      ) : null}

      <ListGroup title="Detalhes">
        {payload.adherence && payload.adherence.sessions.length > 0 ? (
          <SheetRow
            title="Planejado × realizado"
            subtitle="Cardio prescrito no Modelo × feito depois da força"
            value={`${payload.adherence.sessions.length} sessões`}
            sheetTitle="Planejado × realizado"
          >
            <ListGroup title="Semanas">
              {payload.adherence.weeks.map((w) => (
                <ListRow
                  key={w.week_start}
                  title={`Semana de ${fmtDate(w.week_start)}`}
                  subtitle={`${fmtInt(w.done_sessions)} de ${fmtInt(w.planned_sessions)} sessões · ${w.planned_minutes == null ? EM_DASH : fmtInt(w.planned_minutes)} min previstos · ${w.realized_minutes == null ? EM_DASH : fmtInt(w.realized_minutes)} feitos`}
                  value={fmtPercent(w.time_completion_pct)}
                />
              ))}
            </ListGroup>
            <ListGroup title="Sessões">
              {payload.adherence.sessions.map((s) => (
                <ListRow
                  key={`${s.activity_id}-${s.local_date}`}
                  title={`${fmtDate(s.local_date)} · ${s.prescription_text ?? EM_DASH}`}
                  subtitle={`${s.planned_minutes == null ? EM_DASH : fmtInt(s.planned_minutes)} / ${s.realized_minutes == null ? EM_DASH : fmtInt(s.realized_minutes)} min · tempo ${fmtPercent(s.time_completion_pct)} · zona ${s.has_zone_data ? fmtPercent(s.zone_completion_pct) : EM_DASH}`}
                  trailing={<Badge color={adherenceStatusColor(s.status)} variant="light" size="sm">{s.status_label}</Badge>}
                />
              ))}
            </ListGroup>
          </SheetRow>
        ) : null}

        {payload.zones ? (
          <SheetRow
            title="Zonas de FC em uso"
            subtitle={`Origem: ${zoneSourceLabel(payload.zones.source)}`}
            value={payload.zones.max_hr_bpm != null ? `FCmáx ${fmtInt(payload.zones.max_hr_bpm)}` : undefined}
            sheetTitle="Zonas de FC em uso"
          >
            <ListGroup
              footer={
                payload.zones.zones.length === 0
                  ? 'Sem zonas definidas. Configure o Intervals.icu ou informe a FCmáx no Perfil.'
                  : 'As zonas que o motor usa na prescrição, nos minutos por zona e na aderência.'
              }
            >
              {payload.zones.zones.map((z) => (
                <ListRow key={z.zone} title={z.label} value={`${fmtInt(z.lower_bpm)}–${fmtInt(z.upper_bpm)} bpm`} />
              ))}
              {payload.zones.zones.length === 0 ? <ListRow title="Perfil" subtitle="Informar a FCmáx" href="/perfil" /> : null}
            </ListGroup>
          </SheetRow>
        ) : null}

        {payload.by_sport.length > 0 ? (
          <SheetRow title="Por modalidade" value={`${payload.by_sport.length}`} subtitle="Da que tomou mais tempo para a que tomou menos" sheetTitle="Cardio por modalidade">
            <ListGroup>
              {payload.by_sport.map((s) => (
                <ListRow
                  key={s.sport_type}
                  title={sportLabel(s.sport_type)}
                  subtitle={`${fmtInt(s.sessions)} sessões · ${s.distance_km == null ? EM_DASH : `${fmtDecimal(s.distance_km, 2)} km`} · FC média ${s.avg_hr_bpm == null ? EM_DASH : `${fmtDecimal(s.avg_hr_bpm, 0)} bpm`}`}
                  value={`${fmtInt(s.minutes)} min`}
                />
              ))}
            </ListGroup>
          </SheetRow>
        ) : null}

        <SheetRow
          title="FC por sessão de treino"
          subtitle={`${fmtInt(bloco.sessions_with_hr)} de ${fmtInt(bloco.sessions_total)} com FC · cobertura ${bloco.coverage_pct == null ? EM_DASH : `${fmtDecimal(bloco.coverage_pct, 1)}%`}`}
          value={num(bloco.avg_hr_bpm, 'bpm')}
          sheetTitle="FC por sessão de treino"
        >
          <ListGroup footer="A FC medida na própria sessão quando existe; senão, a da atividade do Intervals.icu casada pelo horário de início (tolerância de 60 minutos), que é associação por proximidade, não medida.">
            <ListBlock>
              <StatGrid>
                <StatTile label="FC média das sessões" value={num(bloco.avg_hr_bpm, 'bpm')} />
                <StatTile label="FC máxima registrada" value={num(bloco.max_hr_bpm, 'bpm')} />
              </StatGrid>
              {bloco.from_activity_total > 0 ? (
                <p className={styles.meta}>{plural(bloco.from_activity_total, 'medida no treino', 'medidas no treino')}: FC medida na própria sessão (importada do relógio), não depende do casamento com o Intervals.icu.</p>
              ) : null}
              {bloco.ambiguous_in_use_total > 0 ? (
                <p className={styles.meta}>{plural(bloco.ambiguous_in_use_total, 'associação', 'associações')} ambígua(s): mais de uma atividade caiu na janela; vale a mais próxima no tempo.</p>
              ) : null}
              {payload.unmatched_strength_activities_total > 0 ? (
                <p className={styles.meta}>
                  {fmtInt(payload.unmatched_strength_activities_total)} de {plural(payload.strength_activities_total, 'sessão', 'sessões')} do Intervals sem correspondência: o treino não foi registrado aqui ou o horário ficou fora da tolerância.
                </p>
              ) : null}
            </ListBlock>
          </ListGroup>
          {bloco.items.length === 0 ? (
            <EmptyState title="Nenhuma sessão de treino neste período" description="Sem sessão registrada não há FC de sessão para mostrar." />
          ) : (
            <ListGroup>
              {bloco.items.map((i) => {
                const selo = matchBadge(i);
                const delta = matchDeltaLabel(i.match_delta_seconds);
                return (
                  <ListRow
                    key={i.activity_id ?? `${i.local_date}-${i.name}`}
                    title={`${i.local_date ? fmtDate(i.local_date) : EM_DASH} · ${i.name || 'Treino'}`}
                    subtitle={`${i.minutes == null ? EM_DASH : fmtInt(i.minutes)} min · máx ${i.max_hr_bpm == null ? EM_DASH : `${fmtDecimal(i.max_hr_bpm, 0)} bpm`} · ${selo.label}: ${selo.hint}${delta ? ` (${delta})` : ''}`}
                    value={i.avg_hr_bpm == null ? EM_DASH : `${fmtDecimal(i.avg_hr_bpm, 0)} bpm`}
                  />
                );
              })}
            </ListGroup>
          )}
        </SheetRow>

        {payload.sessions.length > 0 ? (
          <SheetRow title="Atividades de cardio" value={`${payload.sessions.length}`} subtitle="Mais recente primeiro" sheetTitle="Atividades de cardio">
            {dias.map((dia) => (
              <ListGroup key={dia.activity_date} title={dia.activity_date === 'sem-data' ? 'Sem data' : fmtDate(dia.activity_date)}>
                {dia.sessions.map((s) => (
                  <ListRow
                    key={s.external_id}
                    title={s.name || 'Atividade'}
                    subtitle={`${sportLabel(s.sport_type)} · ${s.distance_km == null ? EM_DASH : `${fmtDecimal(s.distance_km, 2)} km`} · ${s.avg_hr_bpm == null ? EM_DASH : `${fmtDecimal(s.avg_hr_bpm, 0)} bpm`}`}
                    value={s.minutes == null ? EM_DASH : `${fmtInt(s.minutes)} min`}
                  />
                ))}
              </ListGroup>
            ))}
          </SheetRow>
        ) : null}
      </ListGroup>

      {!hasAnythingToShow(payload) ? (
        <EmptyState
          title="Nada de cardio e nenhuma FC de sessão neste período"
          description="Ausência de registro não é ausência de treino: só quer dizer que nada chegou do Intervals.icu nesta janela."
        />
      ) : null}
    </>
  );
}
