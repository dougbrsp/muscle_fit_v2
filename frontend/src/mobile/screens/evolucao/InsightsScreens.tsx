'use client';

import { Badge, Button, Switch } from '@mantine/core';
import { IconBrain } from '@tabler/icons-react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { Suspense, useEffect, useMemo, useState } from 'react';
import { AiMarkdownLazy as AiMarkdown, preloadMarkdown } from '@/components/LazyMarkdown';
import { StatusBadge } from '@/components/dashboard/StatusBadge';
import { ApiError } from '@/lib/api';
import { dispatchAndPollJob } from '@/lib/async-job-poll';
import { cooldownBadge } from '@/lib/dashboard-checkin';
import { dashboardKeys, fetchInsights } from '@/lib/dashboard-api';
import { useDashboardFilters, type DashboardFilters } from '@/lib/dashboard-filters';
import { INSIGHTS_BULK_LIMIT, needsServerInsightsPage, sliceInsightsPage } from '@/lib/dashboard-insights-page';
import { fmtDateTime, fmtInt } from '@/lib/dashboard-format';
import { NO_ACTIONS_MESSAGE, insightHref, insightSections } from '@/lib/dashboard-overview';
import type { Insight } from '@/lib/dashboard-overview-types';
import type { InsightsGenerateRequest, InsightsGenerateResponse } from '@/lib/insights-types';
import {
  EmptyState,
  ListBlock,
  ListGroup,
  ListRow,
  ScreenHeader,
  StickyAction,
  useOnlineStatus,
} from '../../ui';
import styles from './evolucao.module.css';
import { plural } from './pure';
import { BlockSkeleton, mobileSafeHref, OfflineNote, PartialNote, PeriodFilter, QueryGate } from './shared';

const PAGE_SIZE = 20;

/** Um insight (os sete campos do contrato) como bloco de lista; cooldown escrito, sem Tooltip. */
export function MobileInsightBlock({ insight, filters }: { insight: Insight; filters: DashboardFilters }) {
  const sections = insightSections(insight);
  const href = mobileSafeHref(insightHref(insight, filters));
  const cooldown = insight.cooldown;
  const selo = cooldownBadge(cooldown);
  return (
    <ListBlock>
      <div className={styles.badges}>
        <StatusBadge domain="severity" code={insight.severity} size="xs" />
        <StatusBadge domain="dataQuality" code={insight.data_quality} size="xs" />
        {selo && cooldown ? (
          <Badge size="xs" variant="light" color={selo.color}>
            {selo.label}
          </Badge>
        ) : null}
      </div>
      <p className={styles.meta} style={{ marginTop: 0, fontWeight: 600 }}>{insight.question}</p>
      {sections.map((s) => (
        <div key={s.key} style={{ marginTop: 10 }}>
          <p className={styles.meta} style={{ margin: 0, textTransform: 'uppercase', fontWeight: 600 }}>{s.label}</p>
          {s.lines.map((line) => (
            <p key={line} className={s.key === 'observation' ? styles.headline : styles.body} style={{ marginTop: 2 }}>
              {line}
            </p>
          ))}
        </div>
      ))}
      {selo && cooldown ? (
        <p className={styles.meta}>
          {cooldown.suppressed
            ? `Você já viu este alerta${cooldown.last_emitted_at ? ` em ${fmtDateTime(cooldown.last_emitted_at)}` : ''}. Ele volta a aparecer ${
                cooldown.snoozed_until ? `depois de ${fmtDateTime(cooldown.snoozed_until)}` : 'na próxima mudança'
              }, ou antes se piorar.`
            : `Mostrado ${cooldown.emitted_count}x; silenciado ${cooldown.suppressed_count}x.`}
        </p>
      ) : null}
    </ListBlock>
  );
}

// ---------------------------------------------------------------------------
// /dashboard/insights
// ---------------------------------------------------------------------------

export function InsightsDashboardScreen() {
  return (
    <Suspense fallback={<BlockSkeleton rows={4} label="Carregando sugestões" />}>
      <InsightsDashboardContent />
    </Suspense>
  );
}

function InsightsDashboardContent() {
  const { filters, setFilters, windowQuery, rangeError } = useDashboardFilters();
  const [offset, setOffset] = useState(0);
  const [silenciados, setSilenciados] = useState(false);

  useEffect(() => {
    setOffset(0);
  }, [windowQuery.window, windowQuery.date_from, windowQuery.date_to, silenciados]);

  const lista = useQuery({
    queryKey: [...dashboardKeys.insights(windowQuery, INSIGHTS_BULK_LIMIT, 0), silenciados],
    queryFn: () => fetchInsights(windowQuery, INSIGHTS_BULK_LIMIT, 0, silenciados),
  });
  const bulk = lista.data;
  const precisaServidor = bulk !== undefined && needsServerInsightsPage(bulk, offset, PAGE_SIZE);
  const paginaServidor = useQuery({
    queryKey: [...dashboardKeys.insights(windowQuery, PAGE_SIZE, offset), silenciados],
    queryFn: () => fetchInsights(windowQuery, PAGE_SIZE, offset, silenciados),
    enabled: precisaServidor,
  });
  const consulta = precisaServidor ? paginaServidor : lista;
  const payload = useMemo(
    () => (bulk === undefined ? undefined : precisaServidor ? paginaServidor.data : sliceInsightsPage(bulk, offset, PAGE_SIZE)),
    [bulk, precisaServidor, paginaServidor.data, offset],
  );
  const gate = { ...consulta, data: payload };

  return (
    <>
      <ScreenHeader
        title="Sugestões"
        subtitle="Tudo que as regras determinísticas dispararam no período. Nenhuma sugestão muda ficha, meta ou prescrição: você decide."
      />
      <PeriodFilter filters={filters} setFilters={setFilters} rangeError={rangeError} />
      <QueryGate query={gate} title="sugestões" rows={4}>
        {(p) => (
          <>
            {p.cooldown_available && (p.suppressed_total > 0 || silenciados) ? (
              <ListGroup footer="Um alerta já visto e ainda no mesmo estado fica quieto por um período. Se ele piorar, aparece mesmo assim.">
                <ListRow
                  title={silenciados ? 'Mostrando também os que você já viu' : `Mostrar também ${fmtInt(p.suppressed_total)} que você já viu`}
                  trailing={
                    <Switch
                      checked={silenciados}
                      onChange={(e) => setSilenciados(e.currentTarget.checked)}
                      color="brand"
                      aria-label="Mostrar também os que você já viu"
                    />
                  }
                />
              </ListGroup>
            ) : null}
            <p className={styles.note} style={{ marginBottom: 12 }}>
              {plural(p.total, 'sugestão', 'sugestões')} · {fmtInt(p.rules_evaluated)} regras avaliadas
              {p.rules_disabled.length > 0 ? ` · desligadas: ${p.rules_disabled.join(', ')}` : ''}
              {!silenciados && p.suppressed_total > 0 ? ` · ${plural(p.suppressed_total, 'silenciada por já ter sido vista', 'silenciadas por já terem sido vistas')}` : ''}
            </p>
            <PartialNote quality={p.data_quality} limitations={p.limitations} fixHref={null} />
            {p.items.length === 0 ? (
              <EmptyState
                title="Nenhuma sugestão"
                description={
                  p.suppressed_total > 0
                    ? 'Nada novo por aqui: o que as regras encontraram no período você já viu. Ligue a chave acima para rever.'
                    : NO_ACTIONS_MESSAGE
                }
              />
            ) : (
              p.items.map((insight) => (
                <ListGroup key={insight.fingerprint}>
                  <MobileInsightBlock insight={insight} filters={filters} />
                  {mobileSafeHref(insightHref(insight, filters)) ? (
                    <ListRow title={`Abrir ${insight.source_screen}`} href={mobileSafeHref(insightHref(insight, filters)) as string} />
                  ) : null}
                </ListGroup>
              ))
            )}
            {offset > 0 || p.has_more ? (
              <>
                <p className={styles.note} style={{ textAlign: 'center', marginBottom: 0 }}>
                  {fmtInt(offset + 1)}–{fmtInt(offset + p.items.length)} de {fmtInt(p.total)}
                </p>
                <div className={styles.pager}>
                  <Button variant="default" mih={44} disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
                    Anteriores
                  </Button>
                  <Button variant="default" mih={44} disabled={!p.has_more} onClick={() => setOffset(offset + PAGE_SIZE)}>
                    Próximas
                  </Button>
                </div>
              </>
            ) : null}
          </>
        )}
      </QueryGate>
    </>
  );
}

// ---------------------------------------------------------------------------
// /insights (pergunta livre à IA)
// ---------------------------------------------------------------------------

const WINDOW_OPTIONS = [
  { value: '7d', label: '7 dias' },
  { value: '28d', label: '28 dias' },
  { value: '90d', label: '90 dias' },
  { value: '180d', label: '180 dias' },
];

export function InsightsIaScreen() {
  useEffect(preloadMarkdown, []);
  const online = useOnlineStatus();
  const [janela, setJanela] = useState('28d');
  const [pergunta, setPergunta] = useState('Analise meu histórico recente de treino e sugira melhorias práticas.');
  const gerar = useMutation({
    networkMode: 'always', // offline falha na hora; nunca dispara sozinha ao voltar a rede
    mutationFn: (payload: InsightsGenerateRequest) =>
      dispatchAndPollJob<InsightsGenerateResponse>('/insights/generate', {
        method: 'POST',
        body: JSON.stringify(payload),
      }),
  });
  const indisponivel = gerar.isError && gerar.error instanceof ApiError && gerar.error.status === 503;

  return (
    <>
      <ScreenHeader
        title="Insights"
        subtitle="Pergunte livremente. A resposta usa o pacote de dados da janela escolhida (os mesmos números da visão geral, com a confiança declarada) e o método (RAG)."
      />
      <ListGroup>
        <ListBlock>
          <div className={styles.stack}>
            <div className={styles.field}>
              <label className={styles.fieldLabel} htmlFor="mob-ia-janela">Janela de análise</label>
              <select id="mob-ia-janela" className={styles.dateInput} value={janela} onChange={(e) => setJanela(e.currentTarget.value)}>
                {WINDOW_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </div>
            <div className={styles.field}>
              <label className={styles.fieldLabel} htmlFor="mob-ia-pergunta">Pergunta</label>
              <textarea
                id="mob-ia-pergunta"
                className={styles.dateInput}
                style={{ minHeight: 110, padding: 12, resize: 'vertical' }}
                value={pergunta}
                onChange={(e) => setPergunta(e.currentTarget.value)}
              />
            </div>
            {gerar.isPending ? (
              <p className={styles.meta} role="status" style={{ margin: 0 }}>Consultando a IA: pode levar alguns segundos...</p>
            ) : null}
          </div>
        </ListBlock>
      </ListGroup>

      {gerar.isError ? (
        <ListGroup title={indisponivel ? 'IA indisponível no momento' : 'Erro ao gerar insight'}>
          <ListBlock>
            <p className={styles.err} role="alert" style={{ fontSize: 15 }}>
              {indisponivel
                ? 'O backend não está com a integração de IA configurada. Tente novamente mais tarde ou avise um administrador.'
                : gerar.error instanceof Error
                  ? gerar.error.message
                  : 'Não foi possível gerar o insight.'}
            </p>
          </ListBlock>
        </ListGroup>
      ) : null}

      {gerar.data ? (
        <ListGroup title="Resultado">
          <div className={styles.md}>
            <AiMarkdown>{gerar.data.insight}</AiMarkdown>
          </div>
        </ListGroup>
      ) : null}

      <StickyAction>
        <Button
          fullWidth
          size="lg"
          color="brand"
          mih={48}
          leftSection={<IconBrain size={18} />}
          loading={gerar.isPending}
          disabled={!pergunta.trim() || !online}
          onClick={() => gerar.mutate({ question: pergunta, window: janela })}
        >
          Gerar insight
        </Button>
      </StickyAction>
      <OfflineNote what="gerar o insight" />
      <div className={styles.scrollEnd} aria-hidden />
    </>
  );
}
