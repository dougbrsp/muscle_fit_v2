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
import { PartialNote, QueryGate } from './shared';

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
  const [grupo, setGrupo] = useState<MuscleComparatives['by_session'][number] | null>(null);
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
      <p className={styles.sectionTitle}>{SESSION_UNIT_TITLE}</p>
      <p className={styles.note} style={{ marginBottom: 8 }}>{SESSION_UNIT_SUBTITLE}</p>
      <PartialNote quality={comparatives.data_quality} limitations={comparatives.limitations} />

      <ResponsiveDataList
        aria-label="Comparativo por sessão, por grupo"
        variant="cards"
        items={comparatives.by_session}
        getKey={(r) => r.muscle_group_id}
        renderItem={(r) => {
          const slots = pointsByOffset(r.points, COLUNAS);
          const t0 = slots[0];
          return (
            <DataCard
              title={r.muscle_group}
              value={t0 ? `${fmtSets(t0.effective_sets_stimulus)} efetivas` : EM_DASH}
              status={<DeltaText values={slots.map((p) => p?.effective_sets_stimulus ?? null)} />}
              secondary={[
                { label: 'T-0', value: t0 ? fmtDate(t0.local_date) : EM_DASH },
                { label: 'Sessões', value: fmtInt(r.sessions_in_window) },
              ]}
              onClick={() => setGrupo(r)}
            />
          );
        }}
      />
      <BottomSheet opened={grupo !== null} onClose={() => setGrupo(null)} title={grupo?.muscle_group ?? 'Grupo'}>
        {grupo ? (
          <ListGroup footer="Mais volume numa sessão não é bom nem ruim: o status contra a meta está na lista de grupos.">
            {pointsByOffset(grupo.points, COLUNAS).map((p, i) => (
              <ListRow
                key={i}
                title={`${sessionOffsetLabel(i)} · ${p ? fmtDate(p.local_date) : EM_DASH}`}
                subtitle={p ? `RPE ${fmtRpe(p.avg_rpe)} · ${fmtKg(p.tonnage_kg)}` : undefined}
                value={p ? `${fmtSets(p.effective_sets_stimulus)} efetivas` : EM_DASH}
              />
            ))}
          </ListGroup>
        ) : null}
      </BottomSheet>

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
// Tendência por grupo muscular
// ---------------------------------------------------------------------------

const LINE_COLORS = [CHART_COLORS.primary, CHART_COLORS.secondary, '#4DA3FF', '#8B93A6', '#2f9e44', '#B98CFF'];

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
  const [selectedGroups, setSelectedGroups] = useState<string[]>([]);
  const selectedValid = useMemo(() => keepAvailableGroups(selectedGroups, groupOptions), [selectedGroups, groupOptions]);
  const visible = useMemo(() => resolveVisibleGroups(selectedValid, groupOptions), [selectedValid, groupOptions]);
  const periods = useMemo(() => buildTrendPeriods(trendRows), [trendRows]);
  const index = useMemo(() => trendValueIndex(trendRows), [trendRows]);
  const points = useMemo(() => pivotTrend(periods, index, visible), [periods, index, visible]);
  const ranking = useMemo(() => buildPressureRows(periods, index, totals, visible), [periods, index, totals, visible]);
  const isDefault =
    period === DEFAULT_MUSCLE_GROUP_TREND_FILTERS.period &&
    metric === DEFAULT_MUSCLE_GROUP_TREND_FILTERS.metric &&
    days === DEFAULT_MUSCLE_GROUP_TREND_FILTERS.days &&
    includeSecondary === DEFAULT_MUSCLE_GROUP_TREND_FILTERS.includeSecondary &&
    selectedValid.length === 0;

  return (
    <>
      <div className={styles.toolbar} style={{ marginTop: 8 }}>
        <p className={styles.sectionTitle} style={{ flex: 1, margin: 0 }}>
          Tendência por grupo muscular
        </p>
        <FilterButton activeCount={isDefault ? 0 : 1} onClick={open} />
      </div>
      <p className={styles.note} style={{ marginBottom: 8 }}>
        Agregado por {TREND_PERIOD_DESCRIPTION[period] || 'período'} e grupo muscular canônico. Janela e granularidade próprias deste
        bloco: não seguem o período do topo.
      </p>
      <FilterSheet
        opened={sheet}
        onClose={close}
        activeCount={isDefault ? 0 : 1}
        onClear={() => {
          setFilters(DEFAULT_MUSCLE_GROUP_TREND_FILTERS);
          setSelectedGroups([]);
        }}
      >
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
        <div className={styles.field}>
          <span className={styles.fieldLabel}>Grupos exibidos (por padrão, os {DEFAULT_VISIBLE_GROUPS} com maior pressão acumulada)</span>
          {groupOptions.map((g) => (
            <Checkbox
              key={g}
              label={g}
              checked={visible.includes(g)}
              onChange={(e) => {
                const next = e.currentTarget.checked ? [...visible, g] : visible.filter((v) => v !== g);
                setSelectedGroups(next);
              }}
              styles={{ root: { minHeight: 44, display: 'flex', alignItems: 'center' } }}
            />
          ))}
        </div>
      </FilterSheet>
      <QueryGate query={trendQuery} title="tendência por grupo" rows={2}>
        {() =>
          points.length === 0 ? (
            <EmptyState title="Sem dados" description="Nenhum dado disponível para os filtros selecionados." />
          ) : (
            <>
              <div className={styles.gap}>
                <ListGroup>
                  <div className={styles.chartBlock}>
                    <MobileChart summary={`Pressão por ${TREND_PERIOD_DESCRIPTION[period] || 'período'}, grupos exibidos.`} label="Tendência por grupo muscular" height={220}>
                      <LineChart data={points} margin={MOBILE_CHART_MARGIN}>
                        <CartesianGrid stroke={CHART_GRID_STROKE} strokeDasharray="4 4" vertical={false} />
                        <XAxis dataKey="period" {...MOBILE_CHART_AXIS} />
                        <YAxis {...MOBILE_CHART_AXIS} width={44} />
                        <Tooltip
                          contentStyle={CHART_TOOLTIP_CONTENT_STYLE}
                          labelStyle={CHART_TOOLTIP_LABEL_STYLE}
                          itemStyle={CHART_TOOLTIP_ITEM_STYLE}
                          formatter={(v) => formatTrendMetric(Number(v), metric)}
                        />
                        <Legend wrapperStyle={{ fontSize: 11, color: MOBILE_TOKENS.textSecondary }} />
                        {visible.map((g, i) => (
                          <Line key={g} type="monotone" dataKey={g} name={g} stroke={LINE_COLORS[i % LINE_COLORS.length]} strokeWidth={2} dot={false} connectNulls />
                        ))}
                      </LineChart>
                    </MobileChart>
                  </div>
                </ListGroup>
              </div>
              <ListGroup title={`Pressão no período mais recente (${periods[periods.length - 1] || 'sem período'})`} footer="Ranking dos grupos exibidos e variação contra o período anterior.">
                {ranking.map((r, i) => (
                  <ListRow
                    key={r.group}
                    title={`${i + 1}. ${r.group}`}
                    subtitle={`Anterior ${formatTrendMetric(r.previous, metric)} · janela ${formatTrendMetric(r.total, metric)}`}
                    value={formatTrendMetric(r.latest, metric)}
                    trailing={
                      <span className={styles.fieldLabel}>
                        {r.variation == null ? 'Sem base' : `${r.variation > 0 ? '+' : ''}${r.variation.toFixed(1)}%`}
                      </span>
                    }
                  />
                ))}
              </ListGroup>
            </>
          )
        }
      </QueryGate>
    </>
  );
}
