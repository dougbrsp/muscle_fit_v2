'use client';

import { Badge } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { useQuery } from '@tanstack/react-query';
import Link from 'next/link';
import { Suspense, useMemo, useState } from 'react';
import { CartesianGrid, ComposedChart, Line, ReferenceLine, Scatter, Tooltip, XAxis, YAxis } from 'recharts';
import {
  CHART_COLORS,
  CHART_GRID_STROKE,
  CHART_TOOLTIP_CONTENT_STYLE,
  CHART_TOOLTIP_ITEM_STYLE,
  CHART_TOOLTIP_LABEL_STYLE,
} from '@/components/charts/chartTheme';
import { UnifiedE1rmChart } from '@/components/charts/UnifiedE1rmChart';
import { StatusBadge } from '@/components/dashboard/StatusBadge';
import { TrendBadge } from '@/components/dashboard/TrendBadge';
import { TrainingGoalBadge } from '@/components/TrainingGoalBadge';
import {
  dashboardKeys,
  fetchExerciseOptions,
  fetchProgression,
  fetchProgressionByMuscleGroup,
  fetchProgressionSuggestion,
  fetchProgressionTrend,
  fetchStrengthPanel,
} from '@/lib/dashboard-api';
import { useDashboardFilters } from '@/lib/dashboard-filters';
import { performanceTooltip } from '@/lib/dashboard-muscle-performance';
import { EM_DASH, fmtDate, fmtInt, fmtKg, fmtKgTotal, fmtRpe, fmtSignedPercent } from '@/lib/dashboard-format';
import type { ProgressionSessionItem } from '@/lib/dashboard-v2-types';
import { loadConventionLabel } from '@/lib/equipment-exercises-types';
import { exerciseProgressHref, fatigueContextLabel, statusReasonsText } from '@/lib/exercise-progress';
import { INTENSITY_STATUS_COLORS, INTENSITY_STATUS_LABELS } from '@/lib/intensity-status';
import { buildProgressionChart, isSessionE1rmReliable, sessionLoad, sessionRepsText } from '@/lib/progression-chart';
import { strengthPanelRows, topSetCaption, topSetText } from '@/lib/strength-panel';
import {
  ALL_GOALS,
  defaultGoalValue,
  goalQueryParam,
  isHighlightedGoal,
  sessionGoalFilterOptions,
} from '@/lib/training-goal-filter';
import { useTrainingGoals } from '@/lib/training-goals';
import { MOBILE_TOKENS } from '../../tokens';
import {
  BottomSheet,
  DataCard,
  EmptyState,
  ListBlock,
  ListGroup,
  ListRow,
  MOBILE_CHART_AXIS,
  MOBILE_CHART_MARGIN,
  MobileChart,
  ResponsiveDataList,
  ScreenHeader,
  SegmentedFilter,
} from '../../ui';
import styles from './evolucao.module.css';
import { BlockSkeleton, PeriodFilter, QueryGate } from './shared';
import { ExerciseProgressionSummaryCard } from '@/app/(app)/dashboard/progressao/ExerciseProgressionSummaryCard';

export function ProgressaoScreen() {
  return (
    <Suspense fallback={<BlockSkeleton rows={4} label="Carregando progressão" />}>
      <ProgressaoContent />
    </Suspense>
  );
}

function ProgressaoContent() {
  const { filters, setFilters, windowQuery, rangeError } = useDashboardFilters();
  return (
    <>
      <ScreenHeader title="Progressão" subtitle="Por grupo muscular e, abaixo, por exercício com veredito de tendência." />
      <PeriodFilter filters={filters} setFilters={setFilters} rangeError={rangeError} />
      <GruposSection window={windowQuery} />
      <ForcaSection window={windowQuery} />
      <ExercicioSection />
    </>
  );
}

// ---------------------------------------------------------------------------
// Grupos musculares
// ---------------------------------------------------------------------------

type GrupoItem = NonNullable<Awaited<ReturnType<typeof fetchProgressionByMuscleGroup>>['items']>[number];

function GruposSection({ window }: { window: ReturnType<typeof useDashboardFilters>['windowQuery'] }) {
  const query = useQuery({
    queryKey: dashboardKeys.progressionByMuscleGroup(window),
    queryFn: () => fetchProgressionByMuscleGroup(window),
  });
  const [open, setOpen] = useState<GrupoItem | null>(null);
  const [help, { open: openHelp, close: closeHelp }] = useDisclosure(false);

  return (
    <QueryGate query={query} title="progressão por grupo" rows={4}>
      {(data) => (
        <>
          <ExerciseProgressionSummaryCard summary={data.degraded ? null : data.exercise_progression} />
          <ListGroup
            title="Tendência por grupo muscular"
            footer={`Compara a janela atual com a anterior de mesmo tamanho (${fmtDate(data.current_from)}–${fmtDate(data.current_to)} × ${fmtDate(data.previous_from)}–${fmtDate(data.previous_to)}).`}
          >
            <ListRow title="Como ler esta tela" subtitle="Volume, desempenho e status do grupo" onClick={openHelp} />
          </ListGroup>
          <ResponsiveDataList
            aria-label="Progressão por grupo muscular"
            variant="cards"
            items={data.items}
            getKey={(i) => i.muscle_group_id}
            emptyTitle="Sem séries no período"
            emptyDescription="Nenhuma série de trabalho registrada na janela escolhida nem no período anterior."
            renderItem={(i) => (
              <DataCard
                title={i.muscle_group_name ?? `Grupo ${i.muscle_group_id}`}
                value={i.performance_change_pct != null ? fmtSignedPercent(i.performance_change_pct) : EM_DASH}
                status={<StatusBadge domain="muscleGroupProgression" code={i.performance_status} size="xs" />}
                secondary={[
                  { label: 'Volume', value: i.change_pct != null ? fmtSignedPercent(i.change_pct) : EM_DASH },
                  { label: 'Fase', value: i.phase ?? EM_DASH },
                ]}
                onClick={() => setOpen(i)}
              />
            )}
          />
          <BottomSheet opened={open !== null} onClose={() => setOpen(null)} title={open?.muscle_group_name ?? 'Grupo'}>
            {open ? <GrupoDetalhe item={open} degraded={data.degraded === true} minSessions={data.min_sessions_per_window} /> : null}
          </BottomSheet>
          <BottomSheet opened={help} onClose={closeHelp} title="Como ler">
            <ListGroup>
              <ListBlock>
                <p className={styles.body}>
                  <strong>Status do grupo</strong>: agregação ponderada dos status dos exercícios do grupo (mesma regra da home), na janela
                  atual.
                </p>
                <p className={styles.meta}>
                  <strong>Volume</strong>: tonelagem (carga × reps das séries de trabalho e das técnicas, sem aquecimento/ajuste, ponderada
                  pela ativação muscular), comparada pela média por sessão. Menos volume pode ser planejado e não quer dizer regressão.
                  {` Com menos de ${data.min_sessions_per_window} sessões numa das janelas, não há leitura.`}
                </p>
                <p className={styles.meta}>
                  <strong>Desempenho</strong>: o status vem dos exercícios do grupo. O percentual de e1RM é só informativo (melhor e1RM de
                  cada exercício feito nas duas janelas, no mesmo equipamento) e não define o status.
                </p>
              </ListBlock>
            </ListGroup>
          </BottomSheet>
        </>
      )}
    </QueryGate>
  );
}

function GrupoDetalhe({ item, degraded, minSessions }: { item: GrupoItem; degraded: boolean; minSessions: number }) {
  const tooltip = performanceTooltip(item, degraded);
  void minSessions;
  return (
    <>
      <ListGroup title="Status">
        <ListBlock>
          <div className={styles.badges}>
            <StatusBadge domain="muscleGroupVolume" code={item.volume_status} size="sm" />
            <StatusBadge domain="muscleGroupProgression" code={item.performance_status} size="sm" />
          </div>
          <p className={styles.meta} style={{ marginTop: 0 }}>
            Volume {item.change_pct != null ? fmtSignedPercent(item.change_pct) : EM_DASH} (média por sessão; tonelagem total{' '}
            {fmtSignedPercent(item.total_change_pct)}). Desempenho{' '}
            {item.performance_change_pct != null ? fmtSignedPercent(item.performance_change_pct) : EM_DASH}.
          </p>
          {item.performance_exercises_excluded > 0 && item.performance_change_pct != null ? (
            <p className={styles.meta}>{fmtInt(item.performance_exercises_excluded)} atípico(s) fora do cálculo</p>
          ) : null}
        </ListBlock>
      </ListGroup>
      <ListGroup title="Períodos">
        <ListRow
          title="Atual"
          subtitle={`${fmtInt(item.current_sets)} séries · ${fmtInt(item.current_sessions ?? 0)} sessões${item.current_tonnage_per_session_kg != null ? ` · ${fmtKgTotal(item.current_tonnage_per_session_kg)} por sessão` : ''}`}
          value={fmtKgTotal(item.current_tonnage_kg)}
        />
        <ListRow
          title="Anterior"
          subtitle={`${fmtInt(item.previous_sets)} séries · ${fmtInt(item.previous_sessions ?? 0)} sessões${item.previous_tonnage_per_session_kg != null ? ` · ${fmtKgTotal(item.previous_tonnage_per_session_kg)} por sessão` : ''}`}
          value={fmtKgTotal(item.previous_tonnage_kg)}
        />
      </ListGroup>
      {tooltip ? (
        <ListGroup title="Desempenho: detalhe">
          <ListBlock>
            <p className={`${styles.body} ${styles.pre}`}>{tooltip}</p>
          </ListBlock>
        </ListGroup>
      ) : null}
    </>
  );
}

// ---------------------------------------------------------------------------
// Força
// ---------------------------------------------------------------------------

function ForcaSection({ window }: { window: ReturnType<typeof useDashboardFilters>['windowQuery'] }) {
  const { filters } = useDashboardFilters();
  const query = useQuery({
    queryKey: dashboardKeys.strengthPanel(window),
    queryFn: () => fetchStrengthPanel(window),
    retry: false, // painel complementar: erro não derruba a tela
  });
  const rows = useMemo(() => strengthPanelRows(query.data), [query.data]);
  if (rows.length === 0) return null;
  return (
    <ListGroup
      title={`${query.data?.training_goal_label ? `${query.data.training_goal_label}: ` : ''}compostos principais`}
      footer="1RM estimado (Epley + RIR): estimativa, não medida. Top set = carga de topo × mais repetições nas séries de topo. Pontos destacados = sessões de força; deload fica fora da linha."
    >
      {rows.map((row) => (
        <ListBlock key={row.exerciseId}>
          <Link
            href={exerciseProgressHref(row.exerciseId, filters)}
            className={styles.headline}
            style={{ color: MOBILE_TOKENS.accent, textDecoration: 'none' }}
          >
            {row.name}
          </Link>
          <p className={styles.meta}>
            {fmtInt(row.strengthSessions)} sessão(ões) de força · 1RM estimado {fmtKg(row.latestE1rm)}
            {row.latestE1rmDate ? ` (${fmtDate(row.latestE1rmDate)})` : ''} · melhor {fmtKg(row.bestE1rm)}
            {row.bestE1rmReliable === false ? ' (pouco confiável)' : ''}
          </p>
          <p className={styles.meta}>
            Último top set: {topSetText(row.lastTopSet)}
            {topSetCaption(row.lastTopSet) ? ` em ${topSetCaption(row.lastTopSet)}` : ''}
          </p>
          {row.chart.hasSeries ? (
            <div style={{ height: 140, marginTop: 8 }}>
              <UnifiedE1rmChart chart={row.chart} compact />
            </div>
          ) : null}
        </ListBlock>
      ))}
    </ListGroup>
  );
}

// ---------------------------------------------------------------------------
// Detalhe por exercício
// ---------------------------------------------------------------------------

function ExercicioSection() {
  const { filters, setFilters, dayRange } = useDashboardFilters();
  const [picker, { open: openPicker, close: closePicker }] = useDisclosure(false);
  const [sessao, setSessao] = useState<ProgressionSessionItem | null>(null);
  const goals = useTrainingGoals().data;
  const [goalFilter, setGoalFilter] = useState<string>(ALL_GOALS);
  const goal = goalQueryParam(goals, goalFilter);
  const defaultGoal = defaultGoalValue(goals);
  const mappingId = filters.exerciseMappingId;

  const optionsQuery = useQuery({
    queryKey: dashboardKeys.exerciseOptions(),
    queryFn: fetchExerciseOptions,
    staleTime: 5 * 60_000,
  });
  const progressionQuery = useQuery({
    queryKey: dashboardKeys.progression(mappingId, dayRange, goal),
    queryFn: () => fetchProgression(mappingId, dayRange, goal),
    enabled: mappingId != null,
  });
  const trendQuery = useQuery({
    queryKey: dashboardKeys.progressionTrend(mappingId, dayRange, goal),
    queryFn: () => fetchProgressionTrend(mappingId, dayRange, goal),
    enabled: mappingId != null,
  });
  const suggestionQuery = useQuery({
    queryKey: dashboardKeys.progressionSuggestion(mappingId, goal),
    queryFn: () => fetchProgressionSuggestion(mappingId, goal),
    enabled: mappingId != null,
    retry: false,
  });

  const items = useMemo(() => progressionQuery.data?.items ?? [], [progressionQuery.data]);
  const chart = useMemo(
    () => buildProgressionChart(items, (v) => isHighlightedGoal(v, defaultGoal)),
    [items, defaultGoal],
  );
  const trend = trendQuery.data;
  const reasons = useMemo(() => statusReasonsText(trend?.status_reasons), [trend?.status_reasons]);
  const selected = optionsQuery.data?.items.find((o) => o.id === mappingId);
  const selectedName = selected?.exercise_name || selected?.amazfit_name || filters.exerciseName;

  return (
    <>
      <ListGroup title="Detalhe por exercício">
        <ListRow
          title="Exercício"
          value={selectedName ?? 'Selecionar'}
          onClick={openPicker}
        />
        {mappingId != null ? (
          <ListRow title="Ver progresso com comparabilidade" subtitle="Equipamento e convenção de carga da chave atual" href={exerciseProgressHref(mappingId, filters)} />
        ) : null}
      </ListGroup>

      {goals ? (
        <div className={styles.gap}>
          <SegmentedFilter
            label="Objetivo"
            value={goalQueryParam(goals, goalFilter) ?? ALL_GOALS}
            onChange={setGoalFilter}
            options={sessionGoalFilterOptions(goals).slice(0, 4)}
          />
        </div>
      ) : null}

      <ExercicioPicker
        opened={picker}
        onClose={closePicker}
        query={optionsQuery}
        selectedId={mappingId}
        onPick={(id, name) => {
          setFilters({ exerciseMappingId: id, exerciseName: name });
          closePicker();
        }}
      />

      {mappingId == null ? (
        <EmptyState
          title="Escolha um exercício"
          description="Selecione um exercício para ver a evolução de carga, repetições, RPE e 1RM estimado."
        />
      ) : (
        <QueryGate
          query={{
            data: progressionQuery.data && trendQuery.data !== undefined ? progressionQuery.data : undefined,
            isLoading: progressionQuery.isLoading || trendQuery.isLoading,
            isError: progressionQuery.isError || trendQuery.isError,
            error: progressionQuery.error ?? trendQuery.error,
            refetch: () => void Promise.all([progressionQuery.refetch(), trendQuery.refetch()]),
          }}
          title="progressão do exercício"
          rows={3}
        >
          {() => (
            <>
              {trend ? (
                <ListGroup title="Tendência">
                  <ListBlock>
                    <div className={styles.badges}>
                      <StatusBadge domain="exerciseProgression" code={trend.status} size="lg" />
                    </div>
                    {reasons.map((r) => (
                      <p key={r.code} className={styles.body}>
                        • {r.text}
                      </p>
                    ))}
                    {trend.exposures_in_window > 0 ? (
                      <p className={styles.meta}>
                        Comparado em: {trend.equipment_name || 'equipamento não identificado'} · {loadConventionLabel(trend.load_convention)}
                        {trend.load_convention_source === 'default_total' ? ' (assumida)' : ''} ·{' '}
                        {fatigueContextLabel(trend.fatigue_context) ? `condição ${fatigueContextLabel(trend.fatigue_context)} · ` : ''}
                        {fmtInt(trend.comparable_exposures)} de {fmtInt(trend.exposures_in_window)} exposição(ões) no período
                      </p>
                    ) : null}
                    {suggestionQuery.data ? (
                      <p className={styles.body} style={{ marginTop: 10 }}>
                        <strong>Sugestão para a próxima sessão:</strong> carga {suggestionQuery.data.load_target || '-'} · reps{' '}
                        {suggestionQuery.data.reps_target}. {suggestionQuery.data.progression_note}{' '}
                        <TrainingGoalBadge value={suggestionQuery.data.training_goal} onlyNonDefault size="xs" />
                      </p>
                    ) : null}
                  </ListBlock>
                </ListGroup>
              ) : null}

              {chart.points.length > 0 ? (
                <ListGroup title="1RM estimado e carga de topo (kg)">
                  <div className={styles.chartBlock}>
                    <MobileChart
                      summary="Só as sessões comparáveis com o veredito (mesmo equipamento e convenção de carga). Ambas as séries estão em kg."
                      label="1RM estimado e carga de topo"
                      height={220}
                    >
                      <ComposedChart data={chart.points} margin={MOBILE_CHART_MARGIN}>
                        <CartesianGrid stroke={CHART_GRID_STROKE} strokeDasharray="3 3" />
                        <XAxis
                          dataKey="xKey"
                          {...MOBILE_CHART_AXIS}
                          tickFormatter={(k: string) => chart.points.find((p) => p.xKey === k)?.label ?? ''}
                        />
                        <YAxis {...MOBILE_CHART_AXIS} width={44} />
                        <Tooltip
                          contentStyle={CHART_TOOLTIP_CONTENT_STYLE}
                          labelStyle={CHART_TOOLTIP_LABEL_STYLE}
                          itemStyle={CHART_TOOLTIP_ITEM_STYLE}
                          labelFormatter={(k) => chart.points.find((p) => p.xKey === String(k))?.label ?? ''}
                        />
                        {chart.points
                          .filter((p) => p.deload)
                          .map((p) => (
                            <ReferenceLine key={`d-${p.xKey}`} x={p.xKey} stroke={CHART_COLORS.compare} strokeDasharray="4 4" />
                          ))}
                        <Line type="monotone" dataKey="e1rm" name="1RM estimado" stroke={CHART_COLORS.primary} strokeWidth={2} dot={false} connectNulls />
                        <Line type="monotone" dataKey="carga" name="Carga de topo" stroke={CHART_COLORS.compare} strokeWidth={2} dot={false} connectNulls />
                        {chart.points.some((p) => p.e1rmForca != null) ? (
                          <Scatter dataKey="e1rmForca" name="1RM estimado (força)" fill={CHART_COLORS.secondary} />
                        ) : null}
                      </ComposedChart>
                    </MobileChart>
                  </div>
                  <ListBlock>
                    <p className={styles.meta} style={{ marginTop: 0 }}>
                      {chart.otherKeySessions > 0
                        ? `${fmtInt(chart.otherKeySessions)} sessão(ões) em outro equipamento/convenção ficaram fora do gráfico (seguem na lista). `
                        : ''}
                      {chart.unreliableE1rm > 0
                        ? `${fmtInt(chart.unreliableE1rm)} 1RM estimado(s) de séries com mais de 12 repetições omitido(s): pouco confiável(is). `
                        : ''}
                      {(chart.strengthPoints ?? 0) > 0 ? 'Pontos destacados: sessões de força. ' : ''}
                      {(chart.deloadSessions ?? 0) > 0
                        ? `${fmtInt(chart.deloadSessions ?? 0)} sessão(ões) de deload marcada(s) (linha tracejada), fora da linha do 1RM estimado.`
                        : ''}
                    </p>
                  </ListBlock>
                </ListGroup>
              ) : null}

              <ResponsiveDataList
                aria-label="Sessões do exercício"
                variant="cards"
                items={progressionQuery.data?.items}
                getKey={(i) => i.activity_id}
                emptyTitle="Sem sessões"
                emptyDescription="Nenhuma sessão registrada para este exercício no período selecionado."
                renderItem={(i) => (
                  <DataCard
                    title={fmtDate(i.local_date)}
                    value={fmtKg(sessionLoad(i))}
                    status={<TrendBadge evolution={i.evolution} />}
                    secondary={[
                      { label: 'Reps', value: sessionRepsText(i) ?? '-' },
                      { label: 'RPE', value: fmtRpe(i.avg_rpe) },
                    ]}
                    onClick={() => setSessao(i)}
                  />
                )}
              />
              <BottomSheet opened={sessao !== null} onClose={() => setSessao(null)} title={sessao ? `Sessão de ${fmtDate(sessao.local_date)}` : 'Sessão'}>
                {sessao ? <SessaoDetalhe item={sessao} defaultGoal={defaultGoal} /> : null}
              </BottomSheet>
            </>
          )}
        </QueryGate>
      )}
    </>
  );
}

function SessaoDetalhe({ item, defaultGoal }: { item: ProgressionSessionItem; defaultGoal: string | null }) {
  return (
    <ListGroup>
      <ListRow title="Carga (topo)" value={fmtKg(sessionLoad(item))} />
      <ListRow title="Reps (séries de topo)" value={sessionRepsText(item) ?? '-'} />
      <ListRow title="RPE" value={fmtRpe(item.avg_rpe)} />
      <ListRow title="Séries" value={fmtInt(item.sets_count)} />
      <ListRow
        title="1RM estimado"
        subtitle={item.e1rm != null && !isSessionE1rmReliable(item) ? 'pouco confiável (>12 reps)' : undefined}
        value={fmtKg(item.e1rm)}
      />
      <ListBlock>
        <p className={styles.meta} style={{ marginTop: 0 }}>Intensidade</p>
        {item.intensity_status ? (
          <Badge size="md" variant="light" color={INTENSITY_STATUS_COLORS[item.intensity_status] || 'gray'}>
            {INTENSITY_STATUS_LABELS[item.intensity_status] || item.intensity_status}
            {item.rpe_target_effective != null ? ` · alvo ${item.rpe_target_effective}` : ''}
          </Badge>
        ) : (
          <p className={styles.body}>-</p>
        )}
        {fatigueContextLabel(item.fatigue_context) ? (
          <p className={styles.meta}>{fatigueContextLabel(item.fatigue_context)}</p>
        ) : null}
        {item.in_verdict_key === false ? (
          <p className={styles.meta}>Outro equipamento, convenção ou condição de fadiga.</p>
        ) : null}
        {isHighlightedGoal(item.training_goal, defaultGoal) || item.deload_label ? (
          <div className={styles.badges} style={{ marginTop: 8 }}>
            <TrainingGoalBadge value={item.training_goal} onlyNonDefault size="xs" />
            {item.deload_label ? (
              <Badge size="xs" variant="outline" color="gray">
                {item.deload_label}
              </Badge>
            ) : null}
          </div>
        ) : null}
      </ListBlock>
    </ListGroup>
  );
}

type OptionsQuery = ReturnType<typeof useQuery<Awaited<ReturnType<typeof fetchExerciseOptions>>>>;

function ExercicioPicker({
  opened,
  onClose,
  query,
  selectedId,
  onPick,
}: {
  opened: boolean;
  onClose: () => void;
  query: OptionsQuery;
  selectedId: number | null;
  onPick: (id: number | null, name: string | null) => void;
}) {
  const [text, setText] = useState('');
  const [group, setGroup] = useState('');
  const all = useMemo(() => query.data?.items ?? [], [query.data]);
  const groups = useMemo(() => {
    const s = new Set<string>();
    for (const o of all) if (o.primary_group) s.add(o.primary_group);
    return Array.from(s).sort((a, b) => a.localeCompare(b, 'pt-BR'));
  }, [all]);
  const norm = text.trim().toLocaleLowerCase('pt-BR');
  const shown = all.filter(
    (o) =>
      (!group || o.primary_group === group) &&
      (!norm || (o.exercise_name || o.amazfit_name || '').toLocaleLowerCase('pt-BR').includes(norm)),
  );
  return (
    <BottomSheet opened={opened} onClose={onClose} title="Escolher exercício">
      <div className={styles.stack}>
        <input
          type="search"
          className={styles.dateInput}
          placeholder="Buscar exercício"
          aria-label="Buscar exercício"
          value={text}
          onChange={(e) => setText(e.currentTarget.value)}
        />
        <select className={styles.dateInput} aria-label="Grupo muscular" value={group} onChange={(e) => setGroup(e.currentTarget.value)}>
          <option value="">Todos os grupos</option>
          {groups.map((g) => (
            <option key={g} value={g}>
              {g}
            </option>
          ))}
        </select>
        {selectedId != null ? (
          <ListGroup>
            <ListRow title="Limpar seleção" onClick={() => onPick(null, null)} />
          </ListGroup>
        ) : null}
        <ResponsiveDataList
          aria-label="Exercícios"
          items={query.data ? shown : undefined}
          getKey={(o) => o.id}
          isLoading={query.isLoading}
          isError={query.isError}
          onRetry={() => void query.refetch()}
          emptyTitle="Nenhum exercício encontrado"
          renderItem={(o) => (
            <ListRow
              title={o.exercise_name || o.amazfit_name || `Exercício ${o.id}`}
              subtitle={o.primary_group ?? undefined}
              current={o.id === selectedId}
              onClick={() => onPick(o.id, o.exercise_name ?? null)}
            />
          )}
        />
      </div>
    </BottomSheet>
  );
}
