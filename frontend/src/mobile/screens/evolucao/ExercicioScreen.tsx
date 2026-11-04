'use client';

import { Badge } from '@mantine/core';
import { IconBarbell } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { useParams } from 'next/navigation';
import { Suspense, useMemo, useState } from 'react';
import { UnifiedE1rmChart } from '@/components/charts/UnifiedE1rmChart';
import { StatusBadge } from '@/components/dashboard/StatusBadge';
import { ApiError } from '@/lib/api';
import { dashboardKeys, fetchExerciseProgress } from '@/lib/dashboard-api';
import { useDashboardFilters } from '@/lib/dashboard-filters';
import { EM_DASH, fmtDate, fmtDecimal, fmtInt, fmtKg, fmtRpe, fmtSignedPercent } from '@/lib/dashboard-format';
import type { ExerciseProgressResponse, Exposure } from '@/lib/dashboard-v3-types';
import { loadConventionLabel } from '@/lib/equipment-exercises-types';
import {
  COMPARABILITY_MISMATCH_HINTS,
  COMPARABILITY_MISMATCH_LABELS,
  buildExposurePair,
  buildRpeSeries,
  buildTrendSeries,
  buildUnifiedE1rmChart,
  comparabilityMismatch,
  decisionsForKey,
  e1rmReliabilityNote,
  fatigueContextLabel,
  progressionDecisionLabel,
  statusReasonsText,
} from '@/lib/exercise-progress';
import {
  defaultGoalValue,
  goalQueryParam,
  isHighlightedGoal,
  progressionGoalOptions,
  progressionGoalSelectValue,
} from '@/lib/training-goal-filter';
import { useTrainingGoals } from '@/lib/training-goals';
import { useDynamicRouteParam } from '@/lib/use-dynamic-route-param';
import { RpeChart, TrendChart } from '@/app/(app)/dashboard/exercicios/[exerciseId]/ExerciseProgressCharts';
import {
  BottomSheet,
  DataCard,
  ErrorState,
  EmptyState,
  ListBlock,
  ListGroup,
  ListRow,
  ResponsiveDataList,
  ScreenHeader,
  StatGrid,
  StatTile,
} from '../../ui';
import styles from './evolucao.module.css';
import { plural } from './pure';
import { BlockSkeleton, metricText, PartialNote, PeriodFilter, QueryGate } from './shared';

/** `/dashboard/exercicios/[exerciseId]`: mesma consulta e mesma chave do desktop. */
export function ExercicioScreen() {
  return (
    <Suspense fallback={<BlockSkeleton rows={4} label="Carregando exercício" />}>
      <ExercicioContent />
    </Suspense>
  );
}

function ExercicioContent() {
  const params = useParams<{ exerciseId: string }>();
  // `useParams()` devolve o slug `placeholder` no export estático; o id real vem da barra de endereços.
  const rawId = useDynamicRouteParam(params?.exerciseId);
  const exerciseId = rawId != null && /^\d+$/.test(rawId) ? Number(rawId) : null;
  const { filters, setFilters, windowQuery, rangeError } = useDashboardFilters();
  const [goal, setGoal] = useState<string | null>(null);
  const goals = useTrainingGoals().data;
  const goalSelected = progressionGoalSelectValue(goals, goal);

  const query = useQuery({
    queryKey: dashboardKeys.exerciseProgress(exerciseId, windowQuery, goal),
    queryFn: () => fetchExerciseProgress(exerciseId, windowQuery, undefined, goal),
    enabled: exerciseId != null,
    retry: (failureCount, error) => (error instanceof ApiError && error.status === 404 ? false : failureCount < 2),
  });
  const notFound = (query.error instanceof ApiError && query.error.status === 404) || (rawId != null && exerciseId == null);

  return (
    <>
      <ScreenHeader title={query.data ? (query.data.exercise.name || query.data.exercise.amazfit_name || 'Exercício') : 'Exercício'} subtitle="Estou evoluindo neste exercício, em condições comparáveis?" />
      <PeriodFilter
        filters={filters}
        setFilters={setFilters}
        rangeError={rangeError}
        extraCount={goal != null ? 1 : 0}
        onClearExtra={() => setGoal(null)}
        extra={
          goals && goalSelected ? (
            <div className={styles.field}>
              <label className={styles.fieldLabel} htmlFor="mob-objetivo">
                Objetivo da progressão
              </label>
              <select
                id="mob-objetivo"
                className={styles.dateInput}
                value={goalSelected}
                onChange={(e) => setGoal(goalQueryParam(goals, e.currentTarget.value))}
              >
                {progressionGoalOptions(goals).map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </div>
          ) : null
        }
      />

      {notFound ? (
        <ErrorState
          title="Exercício não encontrado"
          description="Este exercício não existe no catálogo canônico. Verifique o link ou escolha outro exercício na tela de Progressão."
        />
      ) : (
        <QueryGate
          query={{
            data: query.data,
            isLoading: exerciseId == null || query.isLoading,
            isError: query.isError,
            error: query.error,
            refetch: query.refetch,
          }}
          title="progresso do exercício"
          rows={4}
        >
          {(d) => <Conteudo data={d} defaultGoal={defaultGoalValue(goals)} />}
        </QueryGate>
      )}
    </>
  );
}

function Conteudo({ data, defaultGoal }: { data: ExerciseProgressResponse; defaultGoal: string | null }) {
  const currentKey = data.comparability_key;
  const reasons = useMemo(() => statusReasonsText(data.status_reasons), [data.status_reasons]);
  const pair = useMemo(() => buildExposurePair(data), [data]);
  const trend = useMemo(() => buildTrendSeries(data.exposures, currentKey), [data.exposures, currentKey]);
  const rpe = useMemo(() => buildRpeSeries(data.exposures), [data.exposures]);
  const unified = useMemo(
    () => buildUnifiedE1rmChart(data.e1rm_series, data.deload_sessions, (v) => isHighlightedGoal(v, defaultGoal)),
    [data.e1rm_series, data.deload_sessions, defaultGoal],
  );
  const decisions = useMemo(() => decisionsForKey(data.progression_decisions, currentKey), [data.progression_decisions, currentKey]);
  const ultima = data.exposures.length > 0 ? data.exposures[data.exposures.length - 1] : null;
  const convencaoAssumida = ultima?.load_convention_source === 'default_total';
  const [exposicao, setExposicao] = useState<Exposure | null>(null);
  const historico = useMemo(() => [...data.exposures].reverse(), [data.exposures]);

  return (
    <>
      <ListGroup>
        <ListBlock>
          <StatGrid>
            <StatTile label="1RM estimado (última)" value={fmtKg(ultima?.e1rm)} hint={ultima?.e1rm != null && ultima.e1rm_reliable === false ? 'pouco confiável' : ultima?.local_date ? fmtDate(ultima.local_date) : undefined} />
            <StatTile label="Melhor carga (período)" value={fmtKg(data.records.best_load_kg?.value)} hint={data.records.best_load_kg?.local_date ? fmtDate(data.records.best_load_kg.local_date) : undefined} />
          </StatGrid>
        </ListBlock>
        <ListBlock>
          <div className={styles.badges}>
            <StatusBadge domain="exerciseProgression" code={data.status} size="lg" />
          </div>
          {fatigueContextLabel(data.fatigue_context) ? (
            <p className={styles.meta} style={{ marginTop: 0 }}>Condição: {fatigueContextLabel(data.fatigue_context)}</p>
          ) : null}
          <p className={styles.body} style={{ marginTop: 8 }}>
            <IconBarbell size={14} aria-hidden /> {ultima?.equipment_name || 'Equipamento não identificado'} ·{' '}
            {loadConventionLabel(currentKey?.load_convention ?? null)}
            {convencaoAssumida ? ' (assumida)' : ''}
          </p>
          {convencaoAssumida ? (
            <p className={styles.meta}>Convenção não cadastrada para este par equipamento/exercício: o sistema assume carga total (D12).</p>
          ) : null}
          {reasons.length > 0 ? (
            <>
              <p className={styles.meta} style={{ fontWeight: 600 }}>Por que este status:</p>
              {reasons.map((r) => (
                <p key={r.code} className={styles.body}>• {r.text}</p>
              ))}
            </>
          ) : null}
          <p className={styles.meta}>
            Período de {fmtDate(data.window.date_from)} a {fmtDate(data.window.date_to)} · {plural(data.exposures.length, 'exposição', 'exposições')} · regra{' '}
            {data.metric_versions.exercise_progression_status ?? 'desconhecida'}
          </p>
        </ListBlock>
      </ListGroup>
      <PartialNote quality={data.data_quality} limitations={data.limitations} />

      {data.status === 'INSUFFICIENT_DATA' ? (
        <EmptyState
          title="Ainda não dá para dizer se você está evoluindo"
          description="A comparação exige DUAS exposições comparáveis (mesmo exercício, mesmo equipamento e mesma convenção de carga) dentro do período selecionado. Registre mais uma sessão neste exercício, ou amplie o período."
        />
      ) : null}

      {pair ? (
        <ListGroup
          title="Última exposição vs. a anterior"
          footer={
            !pair.comparable
              ? pair.previous == null
                ? 'Não há exposição anterior no período: os números da última execução estão abaixo, sem variação.'
                : `Estas duas execuções não são comparáveis (${COMPARABILITY_MISMATCH_LABELS[pair.mismatch as keyof typeof COMPARABILITY_MISMATCH_LABELS]}). Os valores continuam visíveis, mas a variação seria uma conclusão errada, por isso ela não é exibida.`
              : `${fmtDate(pair.previous?.local_date)} → ${fmtDate(pair.current.local_date)}`
          }
        >
          {pair.rows.map((row) => (
            <ListRow
              key={row.code}
              title={row.label}
              subtitle={`Anterior ${metricText(row.previous, row.code === 'e1rm' ? 1 : undefined)}${row.previousDetail ? ` (${row.previousDetail})` : ''}${row.currentDetail ? ` · atual ${row.currentDetail}` : ''}${row.note ? ` · ${row.note}` : ''}`}
              value={metricText(row.current, row.code === 'e1rm' ? 1 : undefined)}
              trailing={
                <span className={styles.fieldLabel}>
                  {row.deltaAbs == null && row.deltaPct == null
                    ? EM_DASH
                    : row.deltaPct != null
                      ? fmtSignedPercent(row.deltaPct)
                      : `${(row.deltaAbs ?? 0) > 0 ? '+' : ''}${row.deltaAbs}`}
                </span>
              }
            />
          ))}
        </ListGroup>
      ) : null}

      <ListGroup
        title={`Tendência ${trend.metric ? `— ${trend.label}` : ''}`}
        footer={`Somente exposições comparáveis com a chave atual (mesmo equipamento e mesma convenção de carga).${trend.excluded > 0 ? ` ${plural(trend.excluded, 'exposição', 'exposições')} ficaram de fora por não serem comparáveis.` : ''}`}
      >
        {trend.metric == null ? (
          <ListBlock><p className={styles.body}>Ainda não há duas exposições comparáveis com valor para desenhar uma linha.</p></ListBlock>
        ) : (
          <div className={styles.chartBlock} role="img" aria-label={`Tendência de ${trend.label}`} style={{ height: 230 }}>
            <TrendChart trend={trend} />
          </div>
        )}
      </ListGroup>

      {unified.hasSeries && (unified.strengthPoints > 0 || unified.deloadSessions > 0) ? (
        <ListGroup
          title="1RM estimado: todos os objetivos"
          footer={`${fmtInt(unified.strengthPoints)} ponto(s) de força · ${plural(unified.deloadSessions, 'sessão', 'sessões')} de deload${unified.otherKeyPoints > 0 ? ` · ${fmtInt(unified.otherKeyPoints)} ponto(s) em outro equipamento/convenção ficaram de fora` : ''}. Pontos destacados: sessões de força. Linhas tracejadas: deload.`}
        >
          <div className={styles.chartBlock} role="img" aria-label="1RM estimado, todos os objetivos" style={{ height: 230 }}>
            <UnifiedE1rmChart chart={unified} />
          </div>
        </ListGroup>
      ) : null}

      <ListGroup
        title="RPE médio por sessão"
        footer="Média das séries de trabalho de cada sessão; aquecimento e série de ajuste ficam fora. RPE é relato, não medida: sono, cafeína e a ordem do exercício mexem nele tanto quanto a carga."
      >
        <ListRow title="Média do período" value={rpe.average != null ? fmtDecimal(rpe.average, 1) : EM_DASH} />
        {rpe.hasSeries ? (
          <div className={styles.chartBlock} role="img" aria-label="RPE médio por sessão" style={{ height: 210 }}>
            <RpeChart rpe={rpe} />
          </div>
        ) : (
          <ListBlock>
            <p className={styles.body}>
              Ainda não há duas sessões comparáveis com RPE registrado para desenhar uma linha.
              {rpe.missing > 0 ? ` ${plural(rpe.missing, 'sessão', 'sessões')} sem RPE.` : ''}
            </p>
          </ListBlock>
        )}
        {rpe.missing > 0 && rpe.hasSeries ? <ListBlock><p className={styles.meta} style={{ marginTop: 0 }}>{plural(rpe.missing, 'sessão', 'sessões')} sem RPE registrado.</p></ListBlock> : null}
      </ListGroup>

      <ListGroup
        title="Recordes comparáveis"
        footer="Melhor marca DENTRO da chave atual: recorde em outro aparelho não é recorde deste. O recorde é um só para todos os objetivos; sessões de deload não contam."
      >
        {[
          { label: 'Melhor 1RM estimado', record: data.records.best_e1rm, nota: e1rmReliabilityNote(data.records.best_e1rm) },
          { label: 'Melhor carga', record: data.records.best_load_kg, nota: null },
        ].map((l) => (
          <ListRow
            key={l.label}
            title={l.label}
            subtitle={[l.record?.local_date ? fmtDate(l.record.local_date) : null, l.nota].filter(Boolean).join(' · ') || (l.record?.value == null ? 'Nenhuma exposição comparável com este valor no período.' : undefined)}
            value={l.record?.value != null ? `${fmtDecimal(l.record.value, 1)} kg` : EM_DASH}
          />
        ))}
      </ListGroup>

      <p className={styles.sectionTitle}>Histórico de exposições</p>
      <p className={styles.note} style={{ marginBottom: 8 }}>
        Cada linha é uma execução do exercício numa sessão. As que não são comparáveis com a chave atual estão marcadas: ficam fora do status e do gráfico.
      </p>
      <ResponsiveDataList
        aria-label="Histórico de exposições"
        variant="cards"
        items={historico}
        getKey={(e) => `${e.activity_id}-${e.position_in_session}`}
        emptyTitle="Nenhuma exposição no período"
        emptyDescription="Não há série de trabalho registrada para este exercício no período selecionado."
        renderItem={(e) => {
          const mismatch = comparabilityMismatch(e);
          return (
            <DataCard
              title={fmtDate(e.local_date)}
              value={fmtKg(e.best_load_kg)}
              status={
                mismatch ? (
                  <Badge color="orange" variant="light" radius="sm" size="sm">
                    Não comparável
                  </Badge>
                ) : undefined
              }
              secondary={[
                { label: 'RPE médio', value: fmtRpe(e.avg_rpe) },
                { label: '1RM est.', value: fmtKg(e.e1rm) },
              ]}
              onClick={() => setExposicao(e)}
            />
          );
        }}
      />
      <BottomSheet opened={exposicao !== null} onClose={() => setExposicao(null)} title={exposicao ? `Execução de ${fmtDate(exposicao.local_date)}` : 'Execução'}>
        {exposicao ? <ExposicaoDetalhe e={exposicao} /> : null}
      </BottomSheet>

      {decisions.length > 0 ? (
        <ListGroup
          title="Decisões do motor de progressão"
          footer="Evidência secundária: o que o motor determinístico decidiu ao fechar cada sessão. O status acima continua vindo da regra de comparabilidade, não daqui."
        >
          {decisions.map((d) => (
            <ListRow
              key={d.id}
              title={`${fmtDate(d.created_at)} · ${progressionDecisionLabel(d.decision)}`}
              subtitle={`${d.phase_before && d.phase_after ? `fase ${d.phase_before} → ${d.phase_after} · ` : ''}${d.engine_version}`}
            />
          ))}
        </ListGroup>
      ) : null}
    </>
  );
}

function ExposicaoDetalhe({ e }: { e: Exposure }) {
  const mismatch = comparabilityMismatch(e);
  return (
    <>
      <ListGroup>
        <ListBlock>
          {mismatch ? (
            <>
              <div className={styles.badges}>
                <Badge color="orange" variant="light" radius="sm">Não comparável · {COMPARABILITY_MISMATCH_LABELS[mismatch]}</Badge>
              </div>
              <p className={styles.meta} style={{ marginTop: 0 }}>{COMPARABILITY_MISMATCH_HINTS[mismatch]}</p>
            </>
          ) : null}
          <p className={styles.body}>{e.equipment_name || 'Equipamento não identificado'} · {loadConventionLabel(e.load_convention)}</p>
          <p className={styles.meta}>
            {e.position_in_session != null ? `${e.position_in_session}º na sessão` : ''}
            {fatigueContextLabel(e.fatigue_context) ? ` · ${fatigueContextLabel(e.fatigue_context)}` : ''}
          </p>
        </ListBlock>
        <ListRow title="Melhor carga" value={fmtKg(e.best_load_kg)} />
        <ListRow title="RPE médio" value={fmtRpe(e.avg_rpe)} />
        <ListRow title="1RM estimado" value={fmtKg(e.e1rm)} />
        <ListRow title="Tonelagem" value={fmtKg(e.tonnage)} />
      </ListGroup>
      <ListGroup title="Séries">
        {e.sets.length === 0 ? (
          <ListBlock><p className={styles.body}>{EM_DASH}</p></ListBlock>
        ) : (
          e.sets.map((s, i) => (
            <ListRow
              key={i}
              title={`${fmtKg(s.weight_kg)} × ${fmtInt(s.reps)}`}
              subtitle={`${s.rpe != null ? `RPE ${fmtRpe(s.rpe)}` : 'sem RPE'}${s.rest_seconds != null ? ` · descanso ${fmtInt(s.rest_seconds)} s` : ''}`}
            />
          ))
        )}
      </ListGroup>
    </>
  );
}
