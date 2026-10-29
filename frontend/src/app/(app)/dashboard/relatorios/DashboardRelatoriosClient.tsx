'use client';

import { Alert, Button, Card, Group, List, Select, SimpleGrid, Stack, Text, TextInput, Title } from '@mantine/core';
import { IconDownload, IconInfoCircle } from '@tabler/icons-react';
import { useState } from 'react';
import { ErrorState } from '@/components/dashboard/states';
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
import { BRAND } from '@/theme';

/**
 * `/dashboard/relatorios` — baixa um relatório em Markdown (`.md`) com os
 * dados do período (resumo + detalhamento diário): treino por grupo muscular,
 * treino por equipamento, sono/HRV/FC, corpo, cardio e ciclos de treino.
 * Substitui o antigo relatório em PDF com IA (`/relatorios`).
 *
 * O arquivo é gerado na hora pelo backend (`GET /reports/markdown`), sem
 * histórico: cada clique gera de novo com os números atuais. Nada é calculado
 * aqui — a tela só escolhe o período e dispara o download.
 */

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

export type DashboardRelatoriosViewProps = {
  period: ReportPeriod;
  dateFrom: string;
  dateTo: string;
  downloading: boolean;
  error: string | null;
  rangeError: string | null;
  onPeriodChange: (period: ReportPeriod) => void;
  onDateFromChange: (value: string) => void;
  onDateToChange: (value: string) => void;
  onDownload: () => void;
};

/** Parte visual, sem estado — renderizável no teste (SSR, sem DOM). */
export function DashboardRelatoriosView({
  period,
  dateFrom,
  dateTo,
  downloading,
  error,
  rangeError,
  onPeriodChange,
  onDateFromChange,
  onDateToChange,
  onDownload,
}: DashboardRelatoriosViewProps) {
  return (
    <Stack gap="lg">
      <div>
        <Title order={2} c={BRAND.textPrimary}>
          Relatórios
        </Title>
        <Text size="sm" c={BRAND.textMuted}>
          Baixe um relatório em Markdown com os seus dados do período, para guardar, imprimir ou levar ao seu treinador.
        </Text>
      </div>

      <Card withBorder radius="md" padding="lg">
        <Stack gap="md">
          <Title order={4} c={BRAND.textPrimary}>
            O que vem no relatório
          </Title>
          <List size="sm" spacing={4} c={BRAND.textPrimary}>
            {CONTEUDO.map((item) => (
              <List.Item key={item}>{item}</List.Item>
            ))}
          </List>
          <Alert color="blue" variant="light" icon={<IconInfoCircle size={18} />}>
            Só dados registrados, sem análise por IA. Os números saem dos mesmos cálculos das abas do Dashboard, e o
            arquivo abre em qualquer editor de texto, no VS Code ou no GitHub.
          </Alert>
        </Stack>
      </Card>

      <Card withBorder radius="md" padding="lg">
        <Stack gap="md">
          <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="md">
            <Select
              label="Período"
              data={REPORT_PERIOD_OPTIONS}
              value={period}
              onChange={(value) => value && onPeriodChange(value as ReportPeriod)}
              allowDeselect={false}
              disabled={downloading}
            />
            {period === 'custom' && (
              <>
                <TextInput
                  type="date"
                  label="Data inicial"
                  value={dateFrom}
                  onChange={(event) => onDateFromChange(event.currentTarget.value)}
                  disabled={downloading}
                />
                <TextInput
                  type="date"
                  label="Data final"
                  value={dateTo}
                  onChange={(event) => onDateToChange(event.currentTarget.value)}
                  disabled={downloading}
                />
              </>
            )}
          </SimpleGrid>
          {period === 'custom' && (
            <Text size="xs" c={rangeError ? 'red' : BRAND.textMuted}>
              {rangeError ?? `Entre ${MIN_REPORT_DAYS} e ${MAX_REPORT_DAYS} dias.`}
            </Text>
          )}
          <Group>
            <Button
              leftSection={<IconDownload size={18} />}
              color="brand"
              onClick={onDownload}
              loading={downloading}
              disabled={period === 'custom' && rangeError !== null}
            >
              Baixar relatório (.md)
            </Button>
            {downloading && (
              <Text size="sm" c={BRAND.textMuted}>
                Gerando o relatório… pode levar alguns segundos.
              </Text>
            )}
          </Group>
          {error && <ErrorState title="Não foi possível gerar o relatório." description={error} />}
        </Stack>
      </Card>
    </Stack>
  );
}

export function DashboardRelatoriosClient() {
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
      await downloadMarkdownReport(
        buildReportPath(period, dateFrom, dateTo),
        reportFilename(period, today, dateFrom, dateTo),
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Não foi possível baixar o relatório. Tente novamente.');
    } finally {
      setDownloading(false);
    }
  }

  return (
    <DashboardRelatoriosView
      period={period}
      dateFrom={dateFrom}
      dateTo={dateTo}
      downloading={downloading}
      error={error}
      rangeError={rangeError}
      onPeriodChange={(value) => {
        setPeriod(value);
        setError(null);
      }}
      onDateFromChange={setDateFrom}
      onDateToChange={setDateTo}
      onDownload={handleDownload}
    />
  );
}
