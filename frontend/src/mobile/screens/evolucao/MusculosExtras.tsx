'use client';

import { Checkbox } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { IconArrowDown, IconArrowRight, IconArrowUp } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { useMemo, useState } from 'react';
import { CartesianGrid, Legend, Line, LineChart, Tooltip, XAxis, YAxis } from 'recharts';
import {
  CHART_COLORS,
  CHART_GRID_STROKE,
  CHART_TOOLTIP_CONTENT_STYLE,
  CHART_TOOLTIP_ITEM_STYLE,
  CHART_TOOLTIP_LABEL_STYLE,
} from '@/components/charts/chartTheme';
import { StatusBadge } from '@/components/dashboard/StatusBadge';
import { apiFetch } from '@/lib/api';
import type { MethodologyRealizedResponse, MuscleRecoveryConsecutiveCheckResponse } from '@/lib/analytics-types';
import { EM_DASH, fmtDate, fmtInt, fmtKg, fmtRpe, fmtSets } from '@/lib/dashboard-format';
import {
  SESSION_UNIT_SUBTITLE,
  SESSION_UNIT_TITLE,
  equipmentLabel,
  pointsByOffset,
  sessionDelta,
  sessionOffsetLabel,
} from '@/lib/dashboard-muscles';
import type { MuscleComparatives, MuscleHeatmapSession } from '@/lib/dashboard-v3-types';
import {
  ACTIVATION_WINDOW_DAYS,
  WEEKLY_STATUS_LABEL,
  buildLast7Days,
  consecutivePairsByBlock,
  dayTooltip,
  formatDayMonth,
  insufficientPairByDay,
  perSessionLabel,
  recoveryLabel,
  sessionTooltip,
  toActivationRows,
  weeklyTooltip,
  type ActivationRow,
  type ConsecutivePair,
} from '@/lib/muscle-activation-7d';
import { DEFAULT_VISIBLE_GROUPS, keepAvailableGroups, resolveVisibleGroups } from '@/lib/muscle-groups-selection';
import {
  DEFAULT_MUSCLE_GROUP_TREND_FILTERS,
  TREND_DAY_OPTIONS,
  TREND_METRIC_OPTIONS,
  TREND_PERIOD_DESCRIPTION,
  TREND_PERIOD_OPTIONS,
  formatTrendMetric,
  isTrendMetric,
  isTrendPeriod,
  muscleGroupTrendPath,
  muscleGroupTrendQueryKey,
  pivotTrend,
  pressureRows as buildPressureRows,
  trendGroupOptions as buildTrendGroupOptions,
  trendGroupTotals as buildTrendGroupTotals,
  trendPeriods as buildTrendPeriods,
  trendValueIndex,
  type MuscleGroupTrendFilters,
} from '@/lib/muscle-group-trend';
import type { MuscleGroupsMappedPayload } from '@/lib/muscle-groups-types';
import { formatDecimal } from '@/lib/volume-classes';
import {
  BottomSheet,
  DataCard,
  EmptyState,
  FilterButton,
  FilterSheet,
  ListBlock,
  ListGroup,
  ListRow,
  MOBILE_CHART_AXIS,
  MOBILE_CHART_MARGIN,
  MobileChart,
  ResponsiveDataList,
} from '../../ui';
import { MOBILE_TOKENS } from '../../tokens';
import styles from './evolucao.module.css';
import { PartialNote, QueryGate, SheetRow } from './shared';

/**
 * Blocos de apoio de Grupos musculares: comparativo por sessão (T-0..T-3,
 * por equipamento, mapa de calor), ativação dos últimos 7 dias e tendência por
 * grupo. Mesmos endpoints e chaves do desktop; tabelas viram listas e folhas.
 */
export function MusculosExtras({ comparatives }: { comparatives: MuscleComparatives | null }) {
  return (
    <>
      {comparatives ? <Comparativos comparatives={comparatives} /> : null}
      <Ativacao7d />
      <Tendencia />
    </>
  );
}

// ---------------------------------------------------------------------------
// Comparativo por sessão
// ---------------------------------------------------------------------------

const COLUNAS = 4;

function DeltaText({ values }: { values: Array<number | null> }) {
  const { delta, direction } = sessionDelta(values);
  if (direction === 'none' || delta == null) return <span>sem T-1</span>;
  if (direction === 'stable') {
    return (
      <span>
        <IconArrowRight size={14} aria-hidden /> estável
      </span>
    );
  }
  const Icon = direction === 'up' ? IconArrowUp : IconArrowDown;
  return (
    <span>
      <Icon size={14} aria-hidden /> {delta > 0 ? '+' : ''}
      {delta.toLocaleString('pt-BR', { minimumFractionDigits: 1, maximumFractionDigits: 1 })}
    </span>
  );
}

function Comparativos({ comparatives }: { comparatives: MuscleComparatives }) {
  const [equip, setEquip] = useState<MuscleComparatives['by_equipment'][number] | null>(null);
  if (comparatives.sessions_compared === 0) {
    return (
      <>
        <p className={styles.sectionTitle}>{SESSION_UNIT_TITLE}</p>
        <EmptyState
          title="Nenhuma sessão no período"
          description="O comparativo por sessão precisa de pelo menos um treino registrado no período selecionado."
        />
      </>
    );
  }
  return (
    <>
      <div id="sec-sessao" />
      <p className={styles.sectionTitle}>{SESSION_UNIT_TITLE}</p>
      <p className={styles.note} style={{ marginBottom: 8 }}>{SESSION_UNIT_SUBTITLE}</p>
      <PartialNote quality={comparatives.data_quality} limitations={comparatives.limitations} />

      <ListGroup>
        <SheetRow
          title="Comparativo por grupo (T-0 a T-3)"
          subtitle={`${comparatives.by_session.length} grupos · ${comparatives.sessions_compared} sessões comparadas`}
          sheetTitle="Comparativo por sessão"
        >
          <ListGroup footer="Mais volume numa sessão não é bom nem ruim: o status contra a meta está na lista de grupos.">
            {comparatives.by_session.map((r) => {
              const slots = pointsByOffset(r.points, COLUNAS);
              const t0 = slots[0];
              return (
                <SheetRow
                  key={r.muscle_group_id}
                  title={r.muscle_group}
                  subtitle={`T-0 ${t0 ? fmtDate(t0.local_date) : EM_DASH} · ${fmtInt(r.sessions_in_window)} sessões`}
                  value={t0 ? `${fmtSets(t0.effective_sets_stimulus)} efetivas` : EM_DASH}
                  sheetTitle={r.muscle_group}
                >
                  <ListGroup>
                    {slots.map((p, i) => (
                      <ListRow
                        key={i}
                        title={`${sessionOffsetLabel(i)} · ${p ? fmtDate(p.local_date) : EM_DASH}`}
                        subtitle={p ? `RPE ${fmtRpe(p.avg_rpe)} · ${fmtKg(p.tonnage_kg)}` : undefined}
                        value={p ? `${fmtSets(p.effective_sets_stimulus)} efetivas` : EM_DASH}
                      />
                    ))}
                  </ListGroup>
                </SheetRow>
              );
            })}
          </ListGroup>
        </SheetRow>
      </ListGroup>

      {comparatives.by_equipment.length > 0 ? (
        <ListGroup
          title="Por equipamento"
          footer="As sessões T-0..T-3 são as mesmas do grupo. Um traço significa que o equipamento não foi usado naquela sessão."
        >
          {comparatives.by_equipment.map((g) => (
            <ListRow
              key={g.muscle_group_id}
              title={g.muscle_group}
              subtitle={`${fmtInt(g.equipments.length)} equipamento(s)${g.equipment_truncated ? ` · lista cortada: mais de ${g.equipment_limit} equipamentos, mostra os que mais contribuíram` : ''}`}
              onClick={() => setEquip(g)}
            />
          ))}
        </ListGroup>
      ) : null}
      <BottomSheet opened={equip !== null} onClose={() => setEquip(null)} title={equip?.muscle_group ?? 'Equipamentos'}>
        {equip
          ? equip.equipments.map((row) => (
              <ListGroup
                key={row.equipment_name ?? '__sem__'}
                title={equipmentLabel(row.equipment_name)}
                footer={`${fmtSets(row.effective_sets_stimulus)} efetivas no recorte${row.share_of_group != null ? ` · ${Math.round(row.share_of_group * 100)}% do grupo` : ''}`}
              >
                {pointsByOffset(row.points, COLUNAS).map((p, i) => (
                  <ListRow
                    key={i}
                    title={sessionOffsetLabel(i)}
                    subtitle={p ? `RPE ${fmtRpe(p.avg_rpe)}` : undefined}
                    value={p ? fmtSets(p.effective_sets_stimulus) : EM_DASH}
                  />
                ))}
              </ListGroup>
            ))
          : null}
      </BottomSheet>

      <Heatmap sessions={comparatives.heatmap} />
    </>
  );
}

function Heatmap({ sessions }: { sessions: MuscleHeatmapSession[] }) {
  const [activityId, setActivityId] = useState<string | null>(null);
  const selected = useMemo(() => {
    if (sessions.length === 0) return null;
    return sessions.find((s) => String(s.activity_id) === activityId) ?? sessions[0];
  }, [sessions, activityId]);
  if (!selected) return null;
  return (
    <ListGroup
      title="Mapa de calor da sessão"
      footer="A intensidade é relativa ao grupo mais trabalhado DESTA sessão: compara grupos dentro de um treino, nunca treinos entre si."
    >
      <ListBlock>
        <label className={styles.fieldLabel} htmlFor="mob-heat-sessao">
          Sessão
        </label>
        <select
          id="mob-heat-sessao"
          className={styles.dateInput}
          value={String(selected.activity_id)}
          onChange={(e) => setActivityId(e.currentTarget.value)}
        >
          {sessions.map((s) => (
            <option key={s.activity_id} value={String(s.activity_id)}>
              {fmtDate(s.local_date)}
              {s.activity_name?.trim() ? ` · ${s.activity_name.trim()}` : ''}
            </option>
          ))}
        </select>
      </ListBlock>
      {selected.cells.length === 0 ? (
        <ListBlock>
          <p className={styles.body}>Esta sessão não gerou estímulo atribuído a nenhum grupo.</p>
        </ListBlock>
      ) : (
        selected.cells.map((c) => {
          return (
            <ListRow
              key={c.muscle_group_id}
              title={c.muscle_group}
              subtitle={`RPE ${fmtRpe(c.avg_rpe)}`}
              value={`${fmtSets(c.effective_sets_stimulus)} efetivas`}
              trailing={<StatusBadge domain="muscleHeatmap" code={c.band} size="xs" />}
            />
          );
        })
      )}
    </ListGroup>
  );
}

// ---------------------------------------------------------------------------
// Ativação dos últimos 7 dias
// ---------------------------------------------------------------------------

const ACTIVATION_QUERY_KEY = ['methodology-realized', ACTIVATION_WINDOW_DAYS] as const;
const RECOVERY_CHECK_QUERY_KEY = ['muscle-recovery-consecutive-check', ACTIVATION_WINDOW_DAYS] as const;

function Ativacao7d() {
  const [aberta, setAberta] = useState<ActivationRow | null>(null);
  const activation = useQuery({
    queryKey: ACTIVATION_QUERY_KEY,
    queryFn: () => apiFetch<MethodologyRealizedResponse>('/analytics/methodology-realized'),
  });
  const recovery = useQuery({
    queryKey: RECOVERY_CHECK_QUERY_KEY,
    queryFn: () =>
      apiFetch<MuscleRecoveryConsecutiveCheckResponse>(
        `/analytics/muscle-recovery-consecutive-check?days=${ACTIVATION_WINDOW_DAYS}`,
      ),
  });
  const responseDays = activation.data?.days;
  const days = useMemo(() => responseDays ?? buildLast7Days(), [responseDays]);
  const rows = useMemo(() => toActivationRows(activation.data?.items ?? []), [activation.data]);
  const pairsByBlock = useMemo(
    () => consecutivePairsByBlock(recovery.data?.items ?? [], days, rows),
    [recovery.data, days, rows],
  );
  const pairs: ConsecutivePair[] = aberta ? (pairsByBlock.get(aberta.group) ?? []) : [];
  const insufficientByDay = insufficientPairByDay(pairs);

  return (
    <>
      <div id="sec-ativacao" />
      <p className={styles.sectionTitle}>Ativação muscular (últimos 7 dias)</p>
      <p className={styles.note} style={{ marginBottom: 8 }}>
        Séries diretas por grupo e dia, comparadas com a faixa por treino do macro-grupo na frequência real da semana. Toque num
        grupo para ver os 7 dias.
      </p>
      {recovery.isError ? (
        <p className={styles.note}>Não foi possível consultar a recuperação entre dias consecutivos: o detalhe aparece sem esse destaque.</p>
      ) : null}
      <QueryGate query={activation} title="ativação dos 7 dias" rows={3}>
        {() =>
          rows.length === 0 ? (
            <EmptyState
              title="Nenhum grupo treinado nos últimos 7 dias"
              description="Nenhuma série com grupo muscular mapeado na semana, e nenhum macro-grupo com meta semanal."
            />
          ) : (
            <ResponsiveDataList
              aria-label="Ativação muscular dos últimos 7 dias"
              items={rows}
              getKey={(r) => r.group}
              renderItem={(r) => {
                const insufficient = (pairsByBlock.get(r.group) ?? []).filter((p) => p.item.insufficient_recovery);
                return (
                  <ListRow
                    title={r.group}
                    subtitle={`${r.volumeClassName ?? 'sem macro-grupo'} · ${r.daysTrained}x${r.perSession != null ? ` · ${perSessionLabel(r)}` : ''}${insufficient.length > 0 ? ' · descanso insuficiente entre dias consecutivos' : ''}`}
                    value={`${formatDecimal(r.weeklyTotal)} · ${WEEKLY_STATUS_LABEL[r.weeklyStatus]}`}
                    onClick={() => setAberta(r)}
                  />
                );
              }}
            />
          )
        }
      </QueryGate>
      <BottomSheet opened={aberta !== null} onClose={() => setAberta(null)} title={aberta?.group ?? 'Grupo'}>
        {aberta ? (
          <>
            <ListGroup title="Dias" footer="Séries diretas = exercícios em que o grupo é o alvo principal, sem ponderação.">
              {aberta.days.map((cell, i) => {
                const rec = insufficientByDay.get(i);
                return (
                  <ListRow
                    key={days[i]}
                    title={formatDayMonth(days[i])}
                    subtitle={`${dayTooltip(cell, aberta)}${rec ? ` · ${recoveryLabel(rec)}` : ''}`}
                    value={cell.status ? formatDecimal(cell.directSets) : EM_DASH}
                  />
                );
              })}
            </ListGroup>
            <ListGroup title="Semana">
              <ListBlock>
                <p className={styles.body}>{weeklyTooltip(aberta)}</p>
                {aberta.perSession != null ? <p className={styles.meta}>{sessionTooltip(aberta)}</p> : null}
              </ListBlock>
            </ListGroup>
          </>
        ) : null}
      </BottomSheet>
    </>
  );
}

// ---------------------------------------------------------------------------
// Tendência por grupo muscular (ranking: top 3 + o resto por toque)
// ---------------------------------------------------------------------------

function Tendencia() {
  const [filters, setFilters] = useState<MuscleGroupTrendFilters>(DEFAULT_MUSCLE_GROUP_TREND_FILTERS);
  const [sheet, { open, close }] = useDisclosure(false);
  const update = (patch: Partial<MuscleGroupTrendFilters>) => setFilters((c) => ({ ...c, ...patch }));
  const { period, metric, days, includeSecondary } = filters;
  const trendQuery = useQuery({
    queryKey: muscleGroupTrendQueryKey(filters),
    queryFn: () => apiFetch<MuscleGroupsMappedPayload>(muscleGroupTrendPath(filters)),
  });
  const trendRows = useMemo(() => trendQuery.data?.trend_rows ?? [], [trendQuery.data]);
  const totals = useMemo(() => buildTrendGroupTotals(trendRows), [trendRows]);
  const groupOptions = useMemo(() => buildTrendGroupOptions(totals), [totals]);
  const periods = useMemo(() => buildTrendPeriods(trendRows), [trendRows]);
  const index = useMemo(() => trendValueIndex(trendRows), [trendRows]);
  const ranking = useMemo(() => buildPressureRows(periods, index, totals, groupOptions), [periods, index, totals, groupOptions]);
  const isDefault =
    period === DEFAULT_MUSCLE_GROUP_TREND_FILTERS.period &&
    metric === DEFAULT_MUSCLE_GROUP_TREND_FILTERS.metric &&
    days === DEFAULT_MUSCLE_GROUP_TREND_FILTERS.days &&
    includeSecondary === DEFAULT_MUSCLE_GROUP_TREND_FILTERS.includeSecondary;
  const linha = (r: (typeof ranking)[number], i: number) => (
    <ListRow
      key={r.group}
      title={`${i + 1}. ${r.group}`}
      subtitle={`Anterior ${formatTrendMetric(r.previous, metric)} · janela ${formatTrendMetric(r.total, metric)} · ${r.variation == null ? 'sem base' : `${r.variation > 0 ? '+' : ''}${r.variation.toFixed(1)}%`}`}
      value={formatTrendMetric(r.latest, metric)}
    />
  );

  return (
    <div id="sec-tendencia">
      <div className={styles.toolbar} style={{ marginTop: 8 }}>
        <p className={styles.sectionTitle} style={{ flex: 1, margin: 0 }}>
          Tendência por grupo muscular
        </p>
        <FilterButton activeCount={isDefault ? 0 : 1} onClick={open} />
      </div>
      <p className={styles.note} style={{ marginBottom: 8 }}>
        Agregado por {TREND_PERIOD_DESCRIPTION[period] || 'período'} e grupo muscular canônico. Janela e granularidade próprias deste bloco: não seguem
        o período do topo.
      </p>
      <FilterSheet opened={sheet} onClose={close} activeCount={isDefault ? 0 : 1} onClear={() => setFilters(DEFAULT_MUSCLE_GROUP_TREND_FILTERS)}>
        <div className={styles.field}>
          <label className={styles.fieldLabel} htmlFor="mob-tr-gran">Granularidade</label>
          <select id="mob-tr-gran" className={styles.dateInput} value={period} onChange={(e) => isTrendPeriod(e.currentTarget.value) && update({ period: e.currentTarget.value })}>
            {TREND_PERIOD_OPTIONS.map((o) => (<option key={o.value} value={o.value}>{o.label}</option>))}
          </select>
        </div>
        <div className={styles.field}>
          <label className={styles.fieldLabel} htmlFor="mob-tr-met">Métrica</label>
          <select id="mob-tr-met" className={styles.dateInput} value={metric} onChange={(e) => isTrendMetric(e.currentTarget.value) && update({ metric: e.currentTarget.value })}>
            {TREND_METRIC_OPTIONS.map((o) => (<option key={o.value} value={o.value}>{o.label}</option>))}
          </select>
        </div>
        <div className={styles.field}>
          <label className={styles.fieldLabel} htmlFor="mob-tr-dias">Janela</label>
          <select id="mob-tr-dias" className={styles.dateInput} value={days} onChange={(e) => update({ days: e.currentTarget.value })}>
            {TREND_DAY_OPTIONS.map((o) => (<option key={o.value} value={o.value}>{o.label}</option>))}
          </select>
        </div>
        <Checkbox
          label="Incluir grupos secundários"
          description="Soma também nos grupos secundários e estabilizadores, com o peso cadastrado em cada exercício"
          checked={includeSecondary}
          onChange={(e) => update({ includeSecondary: e.currentTarget.checked })}
        />
      </FilterSheet>
      <QueryGate query={trendQuery} title="tendência por grupo" rows={2}>
        {() =>
          ranking.length === 0 ? (
            <EmptyState title="Sem dados" description="Nenhum dado disponível para os filtros selecionados." />
          ) : (
            <ListGroup
              title={`Pressão no período mais recente (${periods[periods.length - 1] || 'sem período'})`}
              footer="Ranking dos grupos e variação contra o período anterior."
            >
              {ranking.slice(0, 3).map(linha)}
              {ranking.length > 3 ? (
                <SheetRow title={`Ver os outros ${ranking.length - 3} grupos`} sheetTitle="Pressão por grupo">
                  <ListGroup>{ranking.map(linha)}</ListGroup>
                </SheetRow>
              ) : null}
            </ListGroup>
          )
        }
      </QueryGate>
    </div>
  );
}
