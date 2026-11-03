'use client';

import { Button } from '@mantine/core';
import { IconDownload } from '@tabler/icons-react';
import { useState } from 'react';
import { ApiError } from '@/lib/api';
import {
  buildReportPath,
  DEFAULT_REPORT_PERIOD,
  downloadMarkdownReport,
  localTodayIso,
  MAX_REPORT_DAYS,
  MIN_REPORT_DAYS,
  REPORT_PERIOD_OPTIONS,
  reportFilename,
  validateCustomRange,
  type ReportPeriod,
} from '@/lib/report-markdown';
import { ErrorState, ListBlock, ListGroup, ScreenHeader, StickyAction, useOnlineStatus } from '../../ui';
import styles from './evolucao.module.css';

const CONTEUDO = [
  'Cada seção traz o resumo do período e o detalhamento diário: uma linha por data (DD/MM/AAAA), só nos dias com registro.',
  'Treino por grupo muscular: séries físicas, séries efetivas, séries por semana, tonelagem, RPE médio, frequência e meta, com subtotais por macro-grupo e o detalhe de cada dia.',
  'Treino por equipamento: séries, séries efetivas, tonelagem, RPE, carga máxima/média e reps, no período e por dia e exercício.',
  'Sono, HRV, frequência cardíaca de repouso, forma (TSB), dor e check-ins: média, baseline, tendência e os valores de cada dia.',
  'Corpo: peso (início, fim e média móvel), bioimpedância e medidas, com cada pesagem e medição.',
  'Cardio do período inteiro: totais, por modalidade, cada sessão e a frequência cardíaca nos treinos.',
  'Ciclos de treino: Modelos vigentes em cada trecho, adesão, a linha do tempo dos treinos e o Modelo atual com os dias.',
  'Qualidade dos dados: o que ficou de fora e por quê.',
];

/** `/dashboard/relatorios`: mesmas funções de `lib/report-markdown` (período, validação, download). */
export function RelatoriosScreen() {
  const online = useOnlineStatus();
  const [period, setPeriod] = useState<ReportPeriod>(DEFAULT_REPORT_PERIOD);
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const today = localTodayIso();
  const rangeError = period === 'custom' ? validateCustomRange(dateFrom, dateTo, today) : null;

  async function handleDownload() {
    if (rangeError) return;
    setError(null);
    setDownloading(true);
    try {
      await downloadMarkdownReport(buildReportPath(period, dateFrom, dateTo), reportFilename(period, today, dateFrom, dateTo));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Não foi possível baixar o relatório. Tente novamente.');
    } finally {
      setDownloading(false);
    }
  }

  return (
    <>
      <ScreenHeader
        title="Relatórios"
        subtitle="Baixe um relatório em Markdown com os seus dados do período, para guardar ou levar ao seu treinador."
      />
      <ListGroup title="Período">
        <ListBlock>
          <div className={styles.stack}>
            <div className={styles.field}>
              <label className={styles.fieldLabel} htmlFor="mob-rel-periodo">Período</label>
              <select
                id="mob-rel-periodo"
                className={styles.dateInput}
                value={period}
                disabled={downloading}
                onChange={(e) => {
                  setPeriod(e.currentTarget.value as ReportPeriod);
                  setError(null);
                }}
              >
                {REPORT_PERIOD_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>{o.label}</option>
                ))}
              </select>
            </div>
            {period === 'custom' ? (
              <>
                <div className={styles.field}>
                  <label className={styles.fieldLabel} htmlFor="mob-rel-de">Data inicial</label>
                  <input id="mob-rel-de" type="date" className={styles.dateInput} value={dateFrom} disabled={downloading} onChange={(e) => setDateFrom(e.currentTarget.value)} />
                </div>
                <div className={styles.field}>
                  <label className={styles.fieldLabel} htmlFor="mob-rel-ate">Data final</label>
                  <input id="mob-rel-ate" type="date" className={styles.dateInput} value={dateTo} disabled={downloading} onChange={(e) => setDateTo(e.currentTarget.value)} />
                </div>
                <p className={rangeError ? styles.err : styles.meta} style={{ margin: 0 }}>
                  {rangeError ?? `Entre ${MIN_REPORT_DAYS} e ${MAX_REPORT_DAYS} dias.`}
                </p>
              </>
            ) : null}
            {downloading ? <p className={styles.meta} role="status" style={{ margin: 0 }}>Gerando o relatório… pode levar alguns segundos.</p> : null}
            {!online ? <p className={styles.meta} role="status" style={{ margin: 0 }}>Sem conexão: o relatório é gerado pelo servidor. Tente de novo quando a rede voltar.</p> : null}
          </div>
        </ListBlock>
      </ListGroup>

      {error ? <ErrorState title="Não foi possível gerar o relatório" description={error} onRetry={() => void handleDownload()} /> : null}

      <ListGroup
        title="O que vem no relatório"
        footer="Só dados registrados, sem análise por IA. Os números saem dos mesmos cálculos das abas do Dashboard, e o arquivo abre em qualquer editor de texto."
      >
        <ListBlock>
          {CONTEUDO.map((c) => (
            <p key={c} className={styles.body} style={{ marginBottom: 8 }}>• {c}</p>
          ))}
        </ListBlock>
      </ListGroup>

      <StickyAction>
        <Button
          fullWidth
          size="lg"
          color="brand"
          mih={48}
          leftSection={<IconDownload size={18} />}
          loading={downloading}
          disabled={period === 'custom' && rangeError !== null}
          onClick={() => void handleDownload()}
        >
          Baixar relatório (.md)
        </Button>
      </StickyAction>
    </>
  );
}
