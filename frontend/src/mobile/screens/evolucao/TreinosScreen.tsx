'use client';

import { Badge, Button } from '@mantine/core';
import { IconBrain } from '@tabler/icons-react';
import { useMutation, useQuery } from '@tanstack/react-query';
import Link from 'next/link';
import { Suspense, useMemo, useState } from 'react';
import { Bar, BarChart, Line, LineChart, Tooltip, XAxis, YAxis } from 'recharts';
import { AiMarkdown } from '@/components/dashboard/AiMarkdown';
import {
  CHART_TOOLTIP_CONTENT_STYLE,
  CHART_TOOLTIP_ITEM_STYLE,
  CHART_TOOLTIP_LABEL_STYLE,
} from '@/components/charts/chartTheme';
import { StatusBadge } from '@/components/dashboard/StatusBadge';
import {
  dashboardKeys,
  fetchAdherence,
  fetchRpeDistribution,
  fetchTrainingEvolution,
  fetchTrainingTrend,
  fetchWorkoutDetail,
  fetchWorkoutsV3,
  generateWorkoutsAnalysis,
} from '@/lib/dashboard-api';
import {
  dashboardWindowDays,
  useDashboardFilters,
  type DashboardWindow,
} from '@/lib/dashboard-filters';
import { EM_DASH, fmtDate, fmtDuration, fmtInt, fmtKgTotal } from '@/lib/dashboard-format';
import {
  aiAnalysisFailure,
  hasAiSnapshot,
  isoWeekRows,
  rpeRows,
  totalRpeSets,
  WEEKLY_SUMMARY_QUERY_DAYS,
  weeklySummaryTiles,
} from '@/lib/dashboard-training-analytics';
import { textoTecnicaAplicada } from '@/lib/dashboard-technique-indicator';
import {
  groupSessionsBySlot,
  itemStatusEntries,
  ratePercent,
  totalItems,
} from '@/lib/dashboard-workouts';
import type { PlanItem, WorkoutSession } from '@/lib/dashboard-workouts-types';
import { exerciseProgressHref } from '@/lib/exercise-progress';
import {
  BottomSheet,
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
  useOnlineStatus,
} from '../../ui';
import { MOBILE_TOKENS } from '../../tokens';
import { AnaliseIaSnapshotMobile } from './AnaliseIaSnapshotMobile';
import styles from './evolucao.module.css';
import { plural } from './pure';
import { BlockSkeleton, LimitationsNote, metricLimitations, metricText, PartialNote, PeriodFilter, QueryGate } from './shared';

/** Janelas desta tela (as mesmas do desktop): `7d` fica de fora. */
const TREINOS_WINDOWS = ['cycle', '28d', '90d', '180d', 'custom'] as const satisfies readonly DashboardWindow[];
const PAGE_SIZE = 20;

const TOOLTIP = {
  contentStyle: CHART_TOOLTIP_CONTENT_STYLE,
  labelStyle: CHART_TOOLTIP_LABEL_STYLE,
  itemStyle: CHART_TOOLTIP_ITEM_STYLE,
};

export function TreinosScreen() {
  return (
    <Suspense fallback={<BlockSkeleton rows={4} label="Carregando treinos" />}>
      <TreinosContent />
    </Suspense>
  );
}

function TreinosContent() {
  const { filters, setFilters, windowQuery, rangeError } = useDashboardFilters();
  const [offset, setOffset] = useState(0);
  const [aberta, setAberta] = useState<WorkoutSession | null>(null);
  const days = dashboardWindowDays(filters);

  const sessoesQuery = useQuery({
    queryKey: dashboardKeys.workouts(windowQuery, PAGE_SIZE, offset),
    queryFn: () => fetchWorkoutsV3(windowQuery, PAGE_SIZE, offset),
  });
  const sessoes = useMemo(() => sessoesQuery.data?.items ?? [], [sessoesQuery.data]);
  const grupos = useMemo(() => groupSessionsBySlot(sessoes), [sessoes]);

  return (
    <>
      <ScreenHeader title="Treinos" subtitle="O que estava planejado, o que foi executado e o que ficou pelo caminho." />
      <PeriodFilter
        filters={filters}
        setFilters={setFilters}
        rangeError={rangeError}
        allowed={TREINOS_WINDOWS}
        onChange={() => setOffset(0)}
      />

      <Consistencia />
      <ResumoSemana />
      <EvolucaoSemanal days={days} />
      <DistribuicaoRpe days={days} />
      <AnaliseIa days={days} />

      <ListGroup title="Sessões">
        <ListBlock>
          <p className={styles.meta} style={{ marginTop: 0 }}>
            Toque numa sessão para ver o prescrito × executado.
          </p>
        </ListBlock>
      </ListGroup>
      <QueryGate query={sessoesQuery} title="sessões">
        {(data) =>
          sessoes.length === 0 ? (
            <EmptyState title="Nenhum treino no período" description="Nenhum treino registrado no período selecionado." />
          ) : (
            <>
              <PartialNote quality={data.data_quality} limitations={data.limitations ?? []} />
              {grupos.map((grupo) => (
                <ListGroup
                  key={grupo.key}
                  title={grupo.label}
                  footer={
                    (grupo.kind === 'slot'
                      ? `${grupo.templateName ?? 'Modelo sem nome'} · slot ${grupo.slotSequence} · `
                      : '') +
                    `${plural(grupo.sessions.length, 'sessão', 'sessões')}` +
                    (grupo.kind === 'unlinked' ? ' · sem vínculo com ficha' : '') +
                    (grupo.kind === 'no_template' ? ' · ficha sem vínculo com Modelo' : '')
                  }
                >
                  {grupo.sessions.map((s) => (
                    <ListRow
                      key={s.activity_id}
                      title={fmtDate(s.local_date)}
                      subtitle={`${s.name || 'Sem nome'}${s.plan_link?.template_slot_name ? ` · ${s.plan_link.template_slot_name}` : ''}${s.deload_label ? ` · ${s.deload_label}` : ''}`}
                      value={`${fmtInt(s.physical_sets.value)} séries`}
                      onClick={() => setAberta(s)}
                    />
                  ))}
                </ListGroup>
              ))}
              <p className={styles.note}>
                {fmtInt(offset + sessoes.length)} de {fmtInt(data.total)} sessões
              </p>
              <div className={styles.pager}>
                <Button
                  variant="default"
                  mih={44}
                  disabled={offset === 0}
                  onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
                >
                  Anteriores
                </Button>
                <Button variant="default" mih={44} disabled={!data.has_more} onClick={() => setOffset((o) => o + PAGE_SIZE)}>
                  Mais antigas
                </Button>
              </div>
            </>
          )
        }
      </QueryGate>

      <BottomSheet opened={aberta !== null} onClose={() => setAberta(null)} title={aberta ? `Sessão de ${fmtDate(aberta.local_date)}` : 'Sessão'}>
        {aberta ? <SessaoDetalhe session={aberta} /> : null}
      </BottomSheet>
    </>
  );
}

function Consistencia() {
  const { windowQuery } = useDashboardFilters();
  const query = useQuery({
    queryKey: dashboardKeys.adherence(windowQuery),
    queryFn: () => fetchAdherence(windowQuery),
  });
  return (
    <QueryGate query={query} title="consistência" rows={2}>
      {(d) => {
        const pct = ratePercent(d.rate.value);
        return (
          <>
            <ListGroup title="Consistência">
              <ListBlock>
                <p className={styles.headline}>{pct != null ? `${fmtInt(pct)}% do planejado` : 'Consistência não calculável'}</p>
              </ListBlock>
              <ListBlock>
                <StatGrid>
                  <StatTile label="Esperadas" value={metricText(d.expected_sessions, 0)} hint={d.has_template ? undefined : 'sem Modelo'} />
                  <StatTile label="Realizadas" value={metricText(d.completed_sessions, 0)} />
                  <StatTile label="Com ficha" value={metricText(d.planned_sessions, 0)} />
                  <StatTile label="Não executadas" value={metricText(d.missed_sessions, 0)} />
                </StatGrid>
                <p className={styles.meta}>
                  {d.has_template
                    ? `Modelo "${d.template_name ?? 'sem nome'}" · ciclo de ${fmtInt(d.cycle_length_days)} dias`
                    : 'Sem Modelo de Ficha atribuído.'}
                </p>
              </ListBlock>
              {d.slots.length > 0 ? (
                <ListBlock>
                  <p className={styles.meta} style={{ marginTop: 0 }}>
                    Posição no ciclo
                  </p>
                  <div className={styles.chips} style={{ marginTop: 8 }}>
                    {d.slots.map((slot) => (
                      <StatusBadge
                        key={slot.sequence}
                        domain="cycleSlot"
                        code={slot.status}
                        suffix={` · ${slot.name ?? `Slot ${slot.sequence}`}${slot.last_local_date ? ` (${fmtDate(slot.last_local_date)})` : ''}`}
                      />
                    ))}
                  </div>
                  <p className={styles.meta}>
                    Slots do tipo &quot;Normal&quot; contam como treino esperado, mesmo sem exercícios. Dia de descanso precisa estar
                    marcado como &quot;Descanso&quot; no Modelo.
                  </p>
                </ListBlock>
              ) : null}
            </ListGroup>
            <LimitationsNote items={metricLimitations([d.rate, d.expected_sessions, d.completed_sessions, d.planned_sessions, d.missed_sessions])} />
            <PartialNote
              quality={d.data_quality}
              limitations={d.limitations}
              title="Aderência parcial"
              fixHref={d.has_template ? '/dashboard/data-quality' : null}
              fixLabel="Ver qualidade dos dados"
            />
          </>
        );
      }}
    </QueryGate>
  );
}

function ResumoSemana() {
  const query = useQuery({
    queryKey: dashboardKeys.trainingEvolution(WEEKLY_SUMMARY_QUERY_DAYS),
    queryFn: () => fetchTrainingEvolution(WEEKLY_SUMMARY_QUERY_DAYS),
  });
  return (
    <QueryGate query={query} title="resumo da semana" rows={2}>
      {(data) => (
        <ListGroup title="Resumo da semana" footer="Semana corrente: não acompanha o período selecionado.">
          {data.weekly_summary ? (
            <ListBlock>
              <StatGrid>
                {weeklySummaryTiles(data.weekly_summary).map((t) => (
                  <StatTile key={t.key} label={t.label} value={t.value} hint={t.hint} />
                ))}
              </StatGrid>
            </ListBlock>
          ) : (
            <ListBlock>
              <p className={styles.body}>Sem resumo disponível para a semana corrente.</p>
            </ListBlock>
          )}
        </ListGroup>
      )}
    </QueryGate>
  );
}

function EvolucaoSemanal({ days }: { days: number }) {
  const query = useQuery({ queryKey: dashboardKeys.trainingTrend(days), queryFn: () => fetchTrainingTrend(days) });
  const linhas = useMemo(() => isoWeekRows(query.data?.items), [query.data]);
  return (
    <QueryGate query={query} title="evolução semanal" rows={2}>
      {() => (
        <ListGroup title="Evolução por semana ISO">
          <div className={styles.chartBlock}>
            {linhas.length === 0 ? (
              <p className={styles.body} style={{ padding: 4 }}>
                Sem treinos registrados no período selecionado.
              </p>
            ) : (
              <MobileChart
                summary={`Volume por semana de calendário (ISO) nos últimos ${days} dias.`}
                label="Volume por semana ISO"
              >
                <LineChart data={linhas} margin={MOBILE_CHART_MARGIN}>
                  <XAxis dataKey="periodo" {...MOBILE_CHART_AXIS} />
                  <YAxis {...MOBILE_CHART_AXIS} width={52} />
                  <Tooltip {...TOOLTIP} formatter={(v) => [fmtKgTotal(Number(v)), 'Volume']} />
                  <Line type="monotone" dataKey="volume" stroke={MOBILE_TOKENS.accent} strokeWidth={2} dot={{ r: 3 }} />
                </LineChart>
              </MobileChart>
            )}
          </div>
        </ListGroup>
      )}
    </QueryGate>
  );
}

function DistribuicaoRpe({ days }: { days: number }) {
  const query = useQuery({ queryKey: dashboardKeys.rpeDistribution(days), queryFn: () => fetchRpeDistribution(days) });
  const linhas = useMemo(() => rpeRows(query.data?.items), [query.data]);
  return (
    <QueryGate query={query} title="distribuição de RPE" rows={2}>
      {() => (
        <ListGroup title="Distribuição de RPE">
          <div className={styles.chartBlock}>
            {linhas.length === 0 ? (
              <p className={styles.body} style={{ padding: 4 }}>
                Sem séries registradas com RPE no período selecionado.
              </p>
            ) : (
              <MobileChart
                summary={`${plural(totalRpeSets(linhas), 'série', 'séries')} com RPE nos últimos ${days} dias, por nota.`}
                label="Séries por nota de RPE"
              >
                <BarChart data={linhas} margin={MOBILE_CHART_MARGIN}>
                  <XAxis dataKey="rpe" {...MOBILE_CHART_AXIS} />
                  <YAxis {...MOBILE_CHART_AXIS} width={36} allowDecimals={false} />
                  <Tooltip {...TOOLTIP} formatter={(v) => [fmtInt(Number(v)), 'Séries']} labelFormatter={(l) => `RPE ${l}`} />
                  <Bar dataKey="sets" fill={MOBILE_TOKENS.accent} radius={[4, 4, 0, 0]} />
                </BarChart>
              </MobileChart>
            )}
          </div>
        </ListGroup>
      )}
    </QueryGate>
  );
}

function AnaliseIa({ days }: { days: number }) {
  const online = useOnlineStatus();
  const [snapshot, setSnapshot] = useState(false);
  const geracao = useMutation({ networkMode: 'always', mutationFn: (janela: number) => generateWorkoutsAnalysis(janela) });
  const falha = geracao.isError ? aiAnalysisFailure(geracao.error) : null;
  const result = geracao.data ?? null;
  return (
    <ListGroup
      title="Análise com personal trainer IA"
      footer={`Opcional: gera, sob demanda, uma análise dos últimos ${days} dias (não fica salva).`}
    >
      <ListBlock>
        <Button
          fullWidth
          mih={48}
          color="brand"
          leftSection={<IconBrain size={18} />}
          loading={geracao.isPending}
          disabled={!online}
          onClick={() => geracao.mutate(days)}
        >
          Gerar análise
        </Button>
        {geracao.isPending ? (
          <p className={styles.meta} role="status">
            Gerando análise: a IA processa em segundo plano e pode levar até ~90 s. Os outros blocos continuam disponíveis.
          </p>
        ) : null}
        {falha ? (
          <p className={styles.err} role="alert">
            <strong>{falha.title}.</strong> {falha.detail} Os demais blocos desta tela continuam válidos.
          </p>
        ) : null}
      </ListBlock>
      {result ? (
        <>
          {geracao.variables != null && geracao.variables !== days ? (
            <ListBlock>
              <p className={styles.meta} style={{ marginTop: 0 }}>
                Esta análise foi gerada para os últimos {geracao.variables} dias. Gere de novo para o período atual.
              </p>
            </ListBlock>
          ) : null}
          <div className={styles.md}>
            <AiMarkdown>{result.insight}</AiMarkdown>
          </div>
          {hasAiSnapshot(result.training) ? (
            <ListRow title="Dados usados pela IA" subtitle="Tendência, volume, progressão, recordes e recuperação" onClick={() => setSnapshot(true)} />
          ) : null}
        </>
      ) : null}
      <BottomSheet opened={snapshot} onClose={() => setSnapshot(false)} title="Dados usados pela IA">
        {result ? <AnaliseIaSnapshotMobile training={result.training} /> : null}
      </BottomSheet>
    </ListGroup>
  );
}

function SessaoDetalhe({ session }: { session: WorkoutSession }) {
  const { filters } = useDashboardFilters();
  const query = useQuery({
    queryKey: dashboardKeys.workoutDetail(session.activity_id),
    queryFn: () => fetchWorkoutDetail(session.activity_id),
  });
  const entradas = itemStatusEntries(session.item_status_counts);
  return (
    <>
      <ListGroup>
        <ListBlock>
          <div className={styles.badges}>
            <StatusBadge domain="sessionStatus" code={session.session_status} size="sm" />
            {entradas.map((e) => (
              <StatusBadge key={e.code} domain="planItemStatus" code={e.code} size="xs" suffix={` ${e.count}`} />
            ))}
          </div>
          <p className={styles.body}>
            {session.name || 'Sem nome'}
            {session.deload_label ? ` · ${session.deload_label}` : ''}
          </p>
          <p className={styles.meta}>{plural(totalItems(session.item_status_counts), 'item', 'itens')}</p>
        </ListBlock>
        <ListBlock>
          <StatGrid>
            <StatTile label="Séries" value={metricText(session.physical_sets, 0)} />
            <StatTile label="Estímulo" value={metricText(session.effective_sets_stimulus)} />
            <StatTile label="Fadiga" value={metricText(session.effective_sets_fatigue)} />
            <StatTile label="RPE médio" value={metricText(session.avg_rpe)} />
            <StatTile label="Recordes" value={metricText(session.records, 0)} />
            <StatTile label="Progressões" value={metricText(session.progressions, 0)} />
            <StatTile label="Duração" value={fmtDuration(session.duration_seconds)} />
            <StatTile label="Exercícios" value={fmtInt(session.exercises_count)} />
          </StatGrid>
        </ListBlock>
      </ListGroup>
      <LimitationsNote
        items={metricLimitations([session.physical_sets, session.effective_sets_stimulus, session.effective_sets_fatigue, session.avg_rpe, session.records, session.progressions])}
      />
      <QueryGate query={query} title="sessão" rows={3}>
        {(d) => (
          <>
            {d.items.length === 0 ? (
              <EmptyState title="Sem exercícios" description="Nenhum exercício registrado nesta sessão." />
            ) : (
              <ListGroup title="Prescrito × executado">
                {d.items.map((item, i) => (
                  <ListBlock key={`${item.plan_exercise_id ?? 'extra'}-${i}`}>
                    <ItemBloco item={item} href={itemHref(item, filters)} />
                  </ListBlock>
                ))}
              </ListGroup>
            )}
            {d.notes ? <p className={styles.note}>Observações: {d.notes}</p> : null}
            {d.sets_truncated ? (
              <p className={styles.note}>Há séries fora desta lista: a resposta é limitada a {fmtInt(d.sets_limit)} séries.</p>
            ) : null}
            <PartialNote quality={d.data_quality} limitations={d.limitations} />
          </>
        )}
      </QueryGate>
    </>
  );
}

function itemHref(item: PlanItem, filters: ReturnType<typeof useDashboardFilters>['filters']): string | null {
  const id = item.performed?.exercise_id ?? item.substitution?.planned_exercise_id ?? null;
  return id != null ? exerciseProgressHref(id, filters) : null;
}

function ItemBloco({ item, href }: { item: PlanItem; href: string | null }) {
  const ex = item.performed;
  const nome = item.planned_name ?? ex?.name ?? 'Exercício';
  return (
    <div className={styles.stack}>
      <div className={styles.badges} style={{ margin: 0 }}>
        <StatusBadge domain="planItemStatus" code={item.status} size="xs" />
        {item.is_record ? (
          <Badge size="xs" color="teal" variant="light">
            Recorde de carga
          </Badge>
        ) : null}
      </div>
      {href ? (
        <Link href={href} className={styles.headline} style={{ color: MOBILE_TOKENS.accent, textDecoration: 'none' }}>
          {nome}
        </Link>
      ) : (
        <p className={styles.headline}>{nome}</p>
      )}
      {item.is_record ? (
        <p className={styles.meta} style={{ margin: 0 }}>
          Recorde de carga: melhor carga já registrada neste exercício, no mesmo equipamento e convenção de carga (assistido: menos
          assistência). Trocar de máquina não conta.
        </p>
      ) : null}
      {item.status === 'SUBSTITUTED' && item.substitution ? (
        <p className={styles.meta} style={{ margin: 0 }}>
          Prescrito: {item.substitution.planned_name ?? EM_DASH} → executado: {item.substitution.performed_name ?? EM_DASH}
        </p>
      ) : null}
      {item.status === 'INTERRUPTED' && item.missing_sets != null ? (
        <p className={styles.meta} style={{ margin: 0 }}>Faltaram {plural(item.missing_sets, 'série', 'séries')} para o prescrito.</p>
      ) : null}
      {item.technique ? <p className={styles.meta} style={{ margin: 0 }}>{textoTecnicaAplicada(item.technique)}</p> : null}
      <p className={styles.body}>
        <strong>Prescrito: </strong>
        {item.prescribed.sets_count != null ? `${fmtInt(item.prescribed.sets_count)}× ${item.prescribed.reps_target ?? EM_DASH}` : EM_DASH}
        {item.prescribed.load_target ? ` · ${item.prescribed.load_target}` : ''}
        {item.prescribed.rpe_target ? ` · RPE ${item.prescribed.rpe_target}` : ''}
      </p>
      <p className={styles.body}>
        <strong>Executado: </strong>
        {ex ? `${plural(ex.working_sets, 'série', 'séries')} de trabalho` : EM_DASH}
      </p>
      {ex ? (
        <p className={styles.meta} style={{ margin: 0 }}>
          {ex.sets
            .filter((s) => s.is_working)
            .map((s) => `${s.reps ?? EM_DASH}×${s.weight_kg ?? EM_DASH}kg`)
            .join(' · ') || 'Sem série de trabalho'}
        </p>
      ) : null}
      {item.limitations.map((l) => (
        <p key={l} className={styles.meta} style={{ margin: 0 }}>{l}</p>
      ))}
    </div>
  );
}
