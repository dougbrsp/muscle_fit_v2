'use client';

import { Button, Progress } from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { IconSparkles } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { CardioRecommendationPanel } from '@/components/cardio/CardioRecommendationPanel';
import { preloadMarkdown } from '@/components/LazyMarkdown';
import { EffortChart, SleepChart, WellnessChart } from '@/app/(app)/agente-cardio/AgenteCardioCharts';
import { apiFetch } from '@/lib/api';
import type {
  AsyncJob,
  CardioAgentContextResponse,
  CardioGenerationResult,
  CardioRecommendation,
  CardioRecommendationsResponse,
} from '@/lib/analytics-types';
import { statusVisual } from '@/lib/dashboard-status';
import { formatDateOrDash, formatDateTimeOrDash } from '@/lib/format';
import { readinessLabel } from '@/lib/muscle-readiness';
import {
  BottomSheet,
  EmptyState,
  ErrorState,
  ListBlock,
  ListGroup,
  ListRow,
  ResponsiveDataList,
  ScreenHeader,
  StickyAction,
  useOnlineStatus,
} from '../../ui';
import styles from './evolucao.module.css';
import { QueryGate } from './shared';

const DAY_OPTIONS = [
  { value: '7', label: '7 dias' },
  { value: '14', label: '14 dias' },
  { value: '30', label: '30 dias' },
];
const HISTORY_PAGE_SIZE = 20;
const fmt = (v: number | null | undefined, d = 1) => (v != null && Number.isFinite(Number(v)) ? Number(v).toFixed(d) : '—');

/** `/agente-cardio`: mesmas consultas, geração assíncrona (job + polling) e exclusão do desktop. */
export function AgenteCardioScreen() {
  useEffect(preloadMarkdown, []);
  const online = useOnlineStatus();
  const qc = useQueryClient();
  const [days, setDays] = useState('14');
  const [jobId, setJobId] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [rec, setRec] = useState<CardioRecommendation | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<CardioRecommendation | null>(null);
  const [filtros, { open: openFiltros, close: closeFiltros }] = useDisclosure(false);

  const historyQuery = useQuery({
    queryKey: ['cardio-recommendations-history'],
    queryFn: () => apiFetch<CardioRecommendationsResponse>('/cardio-recommendations?limit=100'),
  });
  const contextQuery = useQuery({
    queryKey: ['cardio-agent-context', days],
    queryFn: () => apiFetch<CardioAgentContextResponse>(`/analytics/cardio-agent-context?days=${days}`),
  });
  const generate = useMutation({
    mutationFn: (payload: { days: number }) =>
      apiFetch<{ job_id: string; status: string }>('/cardio-recommendations/generate', {
        method: 'POST',
        body: JSON.stringify(payload),
      }),
    onSuccess: (data) => setJobId(data.job_id),
  });
  const jobQuery = useQuery({
    queryKey: ['cardio-job', jobId],
    queryFn: () => apiFetch<AsyncJob<CardioGenerationResult>>(`/jobs/${jobId}`),
    enabled: !!jobId,
    refetchInterval: (query) => (query.state.data?.status === 'pending' ? 2000 : false),
  });
  const job = jobQuery.data;
  const isGenerating = generate.isPending || (!!jobId && (job?.status === 'pending' || jobQuery.isLoading));
  const generated = job?.status === 'done' ? job.result : undefined;

  useEffect(() => {
    if (job?.status === 'done') qc.invalidateQueries({ queryKey: ['cardio-recommendations-history'] });
  }, [job?.status, jobId, qc]);

  const remover = useMutation({
    mutationFn: (id: number) => apiFetch(`/cardio-recommendations/${id}`, { method: 'DELETE' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['cardio-recommendations-history'] });
      setDeleteTarget(null);
      setRec(null);
    },
  });

  const ctx = contextQuery.data;
  const wellness = useMemo(
    () =>
      [...(ctx?.intervals_wellness || [])].reverse().map((i) => ({
        date: i.wellness_date,
        fitness_ctl: i.fitness_ctl != null ? Number(i.fitness_ctl) : null,
        fatigue_atl: i.fatigue_atl != null ? Number(i.fatigue_atl) : null,
        form_tsb: i.form_tsb != null ? Number(i.form_tsb) : null,
      })),
    [ctx],
  );
  const latestWellness = ctx?.intervals_wellness?.[0];
  const sleep = useMemo(
    () =>
      [...(ctx?.sleep_metrics || [])].reverse().map((i) => ({
        date: i.local_date,
        duration_hours: i.duration_hours != null ? Number(i.duration_hours) : null,
        deep_sleep_hours: i.deep_sleep_hours != null ? Number(i.deep_sleep_hours) : null,
      })),
    [ctx],
  );
  const effort = useMemo(
    () =>
      [...(ctx?.effort_metrics_estimated || [])].reverse().map((i) => ({
        date: i.local_date,
        epoc_7d_estimated: i.epoc_7d_estimated,
        pai_7d_estimated: i.pai_7d_estimated,
      })),
    [ctx],
  );
  const history = historyQuery.data?.items;
  const shown = useMemo(() => (history ? history.slice(0, page * HISTORY_PAGE_SIZE) : undefined), [history, page]);

  return (
    <>
      <ScreenHeader
        title="Agente de Cardio"
        subtitle="Gera uma recomendação de cardio com base no seu contexto recente (recuperação, sono, esforço e carga)."
      />
      <ListGroup>
        <ListRow title="Janela de contexto" value={DAY_OPTIONS.find((o) => o.value === days)?.label} onClick={openFiltros} />
      </ListGroup>
      <BottomSheet opened={filtros} onClose={closeFiltros} title="Janela de contexto">
        <ListGroup>
          {DAY_OPTIONS.map((o) => (
            <ListRow
              key={o.value}
              title={o.label}
              current={o.value === days}
              onClick={() => {
                setDays(o.value);
                closeFiltros();
              }}
            />
          ))}
        </ListGroup>
      </BottomSheet>

      <QueryGate query={contextQuery} title="contexto do agente" rows={3}>
        {() => (
          <>
            <ListGroup
              title={`Carga cardiovascular (Intervals.icu): ${days} dias`}
              footer={latestWellness ? `Rampa: ${fmt(latestWellness.ramp_rate)} · PAI 7 dias (estimado): ${fmt(ctx?.effort_metrics_estimated?.[0]?.pai_7d_estimated, 0)}` : undefined}
            >
              {wellness.length > 0 ? (
                <div className={styles.chartBlock} role="img" aria-label="Aptidão, fadiga e forma por dia">
                  <WellnessChart wellnessChartData={wellness} />
                </div>
              ) : (
                <ListBlock>
                  <p className={styles.body}>Sem dados de Intervals.icu no período. Conecte sua conta em Intervals.icu para ver esta seção.</p>
                </ListBlock>
              )}
              {wellness.length === 0 ? <ListRow title="Abrir Intervals.icu" href="/intervals-icu" /> : null}
            </ListGroup>
            <ListGroup title={`Sono: ${days} dias`}>
              {sleep.length > 0 ? (
                <div className={styles.chartBlock} role="img" aria-label="Sono por dia">
                  <SleepChart sleepChartData={sleep} />
                </div>
              ) : (
                <ListBlock><p className={styles.body}>Sem registros manuais de sono no período. Cadastre em Indicadores físicos.</p></ListBlock>
              )}
            </ListGroup>
            <ListGroup title={`Esforço (EPOC/PAI de 7 dias, estimado): ${days} dias`}>
              {effort.length > 0 ? (
                <div className={styles.chartBlock} role="img" aria-label="Esforço por dia">
                  <EffortChart effortChartData={effort} />
                </div>
              ) : (
                <ListBlock><p className={styles.body}>Sem atividades do Intervals.icu com tempo em zona de FC no período.</p></ListBlock>
              )}
            </ListGroup>
            <ListGroup title="Recuperação muscular (14 dias)" footer="Janela fixa de 14 dias, independente da janela de contexto.">
              {(ctx?.muscle_recovery || []).length > 0 ? (
                (ctx?.muscle_recovery || []).map((item) => (
                  <ListBlock key={item.muscle_group}>
                    <p className={styles.headline}>{item.muscle_group}</p>
                    <Progress
                      value={item.recovery_pct}
                      color={statusVisual('recoveryStatus', item.status).color}
                      radius="xl"
                      mt={8}
                      aria-label={`Recuperação de ${item.muscle_group}: ${fmt(item.recovery_pct, 0)}%`}
                    />
                    <p className={styles.meta}>
                      {fmt(item.recovery_pct, 0)}% · {readinessLabel(item.readiness_status)} · última sessão {formatDateOrDash(item.last_trained_at)}
                    </p>
                  </ListBlock>
                ))
              ) : (
                <ListBlock><p className={styles.body}>Sem dados de recuperação muscular nos últimos 14 dias.</p></ListBlock>
              )}
            </ListGroup>
          </>
        )}
      </QueryGate>

      {generate.isError ? (
        <ErrorState
          title="Erro ao iniciar a geração"
          description={generate.error instanceof Error ? generate.error.message : 'Não foi possível acionar o Agente de Cardio.'}
        />
      ) : null}
      {job?.status === 'failed' ? (
        <ErrorState title="A geração falhou" description={job.error || 'O Agente de Cardio não conseguiu gerar a recomendação. Tente novamente.'} />
      ) : null}
      {isGenerating ? (
        <p className={styles.note} role="status">Gerando recomendação com o Agente de Cardio...</p>
      ) : null}
      {generated ? (
        <ListGroup title={`Recomendação gerada (${formatDateOrDash(generated.reference_date)})`}>
          <div className={styles.md}>
            <CardioRecommendationPanel
              recommendationId={generated.id}
              markdown={generated.recommendation_markdown}
              promptVersion={generated.prompt_version}
            />
          </div>
        </ListGroup>
      ) : null}

      <p className={styles.sectionTitle}>Histórico de recomendações</p>
      <ResponsiveDataList
        aria-label="Histórico de recomendações"
        items={shown}
        getKey={(r) => r.id}
        isLoading={historyQuery.isLoading}
        isError={historyQuery.isError}
        onRetry={() => void historyQuery.refetch()}
        errorTitle="Erro ao carregar histórico"
        emptyTitle="Nenhuma recomendação gerada ainda"
        renderItem={(r) => (
          <ListRow
            title={formatDateOrDash(r.reference_date)}
            subtitle={`Gerado em ${formatDateTimeOrDash(r.generated_at)}${r.model ? ` · ${r.model}` : ''}`}
            onClick={() => setRec(r)}
          />
        )}
      />
      {history && history.length > page * HISTORY_PAGE_SIZE ? (
        <div className={styles.pager}>
          <Button variant="default" mih={44} onClick={() => setPage((p) => p + 1)}>
            Mostrar mais ({history.length - page * HISTORY_PAGE_SIZE} restantes)
          </Button>
        </div>
      ) : null}

      <BottomSheet
        opened={rec !== null}
        onClose={() => setRec(null)}
        title={rec ? `Recomendação de ${formatDateOrDash(rec.reference_date)}` : 'Recomendação'}
        footer={
          rec ? (
            <Button color="red" variant="light" fullWidth mih={44} onClick={() => setDeleteTarget(rec)}>
              Excluir recomendação
            </Button>
          ) : undefined
        }
      >
        {rec ? (
          <div className={styles.md} style={{ padding: 0 }}>
            <CardioRecommendationPanel recommendationId={rec.id} markdown={rec.recommendation_markdown} promptVersion={rec.prompt_version} />
          </div>
        ) : null}
      </BottomSheet>

      {/* cardio_recommendations é log de auditoria imutável: a exclusão exige confirmação explícita. */}
      <BottomSheet
        opened={deleteTarget !== null}
        onClose={() => setDeleteTarget(null)}
        title="Confirmar exclusão"
        footer={
          <>
            <Button variant="default" style={{ flex: 1, minHeight: 44 }} disabled={remover.isPending} onClick={() => setDeleteTarget(null)}>
              Cancelar
            </Button>
            <Button color="red" style={{ flex: 1, minHeight: 44 }} loading={remover.isPending} onClick={() => deleteTarget && remover.mutate(deleteTarget.id)}>
              Excluir
            </Button>
          </>
        }
      >
        <p className={styles.body}>
          Excluir a recomendação de {deleteTarget ? formatDateOrDash(deleteTarget.reference_date) : ''}? Esta ação não pode ser desfeita:
          o histórico é um log de auditoria imutável.
        </p>
        {remover.isError ? (
          <p className={styles.err} role="alert">
            {remover.error instanceof Error ? remover.error.message : 'Não foi possível excluir o registro.'}
          </p>
        ) : null}
      </BottomSheet>

      {!online ? <EmptyState title="Sem conexão" description="A geração e o histórico dependem do servidor. Tente de novo quando a rede voltar." /> : null}

      <StickyAction>
        <Button
          fullWidth
          size="lg"
          color="brand"
          mih={48}
          leftSection={<IconSparkles size={18} />}
          loading={isGenerating}
          onClick={() => {
            setJobId(null);
            generate.mutate({ days: Number(days) });
          }}
        >
          Gerar recomendação
        </Button>
      </StickyAction>
    </>
  );
}
