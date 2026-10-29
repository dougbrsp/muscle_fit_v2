'use client';

import { Alert, Button, Divider, Stack, Text } from '@mantine/core';
import { IconAlertTriangle, IconBrain } from '@tabler/icons-react';
import { useMutation } from '@tanstack/react-query';
import { AiMarkdown } from '@/components/dashboard/AiMarkdown';
import { generateWorkoutsAnalysis } from '@/lib/dashboard-api';
import { aiAnalysisFailure, hasAiSnapshot } from '@/lib/dashboard-training-analytics';
import type { WorkoutsAnalysisGenerateResponse } from '@/lib/workouts-analysis-types';
import { BRAND } from '@/theme';
import { AnaliseIaSnapshot } from './AnaliseIaSnapshot';
import { BlocoCard } from './BlocoCard';

/**
 * "Análise com personal trainer IA" (herdado de `/treinos`). Opt-in por
 * botão e sem persistência: `POST /workouts-analysis/generate` (202 +
 * `job_id`) com polling em `GET /jobs/{job_id}` via `dispatchAndPollJob` —
 * o mesmo contrato e o mesmo timeout (~90 s) da tela antiga.
 *
 * Falha do job fica restrita a este bloco: nenhuma outra consulta da tela
 * depende do resultado da IA.
 *
 * `days` vem do seletor de período do dashboard. O resultado guarda a janela
 * com que foi gerado (`variables`), então trocar o período depois NÃO faz a
 * análise antiga parecer ser da janela nova.
 */
export function AnaliseIaBloco({ days }: { days: number }) {
  const geracao = useMutation<WorkoutsAnalysisGenerateResponse, Error, number>({
    mutationFn: (janela) => generateWorkoutsAnalysis(janela),
  });

  return (
    <AnaliseIaView
      days={days}
      pending={geracao.isPending}
      error={geracao.isError ? geracao.error : null}
      result={geracao.data ?? null}
      resultDays={geracao.variables ?? null}
      onGenerate={() => geracao.mutate(days)}
    />
  );
}

/** Apresentação pura do bloco (testável sem React Query nem rede). */
export function AnaliseIaView({
  days,
  pending,
  error,
  result,
  resultDays,
  onGenerate,
}: {
  days: number;
  pending: boolean;
  error: unknown;
  result: WorkoutsAnalysisGenerateResponse | null;
  resultDays: number | null;
  onGenerate: () => void;
}) {
  const falha = error ? aiAnalysisFailure(error) : null;

  return (
    <BlocoCard
      titulo="Análise com personal trainer IA"
      descricao={`Opcional — gera, sob demanda, uma análise dos últimos ${days} dias (não fica salva).`}
      acao={
        <Button leftSection={<IconBrain size={16} />} color="brand" loading={pending} onClick={onGenerate}>
          Gerar análise
        </Button>
      }
    >
      <Stack gap="md">
        {pending && (
          <Text size="sm" c={BRAND.textMuted}>
            Gerando análise — a IA processa em segundo plano e pode levar até ~90 s. Os outros blocos continuam
            disponíveis enquanto isso.
          </Text>
        )}

        {falha && (
          <Alert color="red" variant="light" icon={<IconAlertTriangle size={18} />} title={falha.title}>
            <Text size="sm">
              {falha.detail} Os demais blocos desta tela continuam válidos — só a análise por IA falhou.
            </Text>
          </Alert>
        )}

        {result && (
          <>
            {resultDays != null && resultDays !== days && (
              <Text size="xs" c={BRAND.textMuted}>
                Esta análise foi gerada para os últimos {resultDays} dias. Gere de novo para o período atual.
              </Text>
            )}
            <div>
              <AiMarkdown>{result.insight}</AiMarkdown>
            </div>
            {hasAiSnapshot(result.training) && (
              <>
                <Divider label="Dados usados pela IA" labelPosition="left" />
                <AnaliseIaSnapshot training={result.training} />
              </>
            )}
          </>
        )}
      </Stack>
    </BlocoCard>
  );
}
