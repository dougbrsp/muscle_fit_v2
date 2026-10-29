'use client';

import {
  Alert,
  Button,
  Group,
  Paper,
  Select,
  Stack,
  Text,
  Textarea,
  Title,
} from '@mantine/core';
import { IconAlertTriangle, IconBrain } from '@tabler/icons-react';
import { useMutation } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { AiMarkdownLazy as AiMarkdown, preloadMarkdown } from '@/components/LazyMarkdown';
import { ApiError } from '@/lib/api';
import { dispatchAndPollJob } from '@/lib/async-job-poll';
import type { InsightsGenerateRequest, InsightsGenerateResponse } from '@/lib/insights-types';
import { BRAND } from '@/theme';

// F3.2: as janelas agora são as do hub (`WindowKind`), não um número de dias
// solto. O backend resolve a janela no fuso do usuário e monta o pacote de
// dados a partir dela — a tela não escolhe mais QUE dados vão para a IA, só o
// período.
const windowOptions = [
  { value: '7d', label: '7 dias' },
  { value: '28d', label: '28 dias' },
  { value: '90d', label: '90 dias' },
  { value: '180d', label: '180 dias' },
];

export default function InsightsPage() {
  // COD-14: o Markdown da IA vem sob demanda; começa a baixar já na montagem.
  useEffect(preloadMarkdown, []);
  const [analysisWindow, setAnalysisWindow] = useState('28d');
  const [question, setQuestion] = useState(
    'Analise meu histórico recente de treino e sugira melhorias práticas.',
  );

  // F3.2: esta tela não monta mais o contexto. Até aqui ela buscava quatro
  // endpoints v1/v2, serializava o JSON num textarea e mandava como texto
  // livre — números sem `nature`, sem versão de fórmula e sem qualidade
  // declarada, que o modelo então recalculava. Agora o servidor monta o
  // `intelligence_package` a partir da MESMA leitura da visão geral, e o
  // cliente envia só a pergunta e a janela.

  // Contrato assíncrono desde a Fase 3: POST /insights/generate responde
  // 202 { job_id }, o resultado final (MESMO shape de antes: { model,
  // insight }) vem do polling em GET /jobs/{job_id} via `dispatchAndPollJob`
  // — mesmo padrão já usado por /workouts-analysis/generate em /treinos, ver
  // `async-job-poll.ts`.
  const generateMutation = useMutation({
    mutationFn: (payload: InsightsGenerateRequest) =>
      dispatchAndPollJob<InsightsGenerateResponse>('/insights/generate', {
        method: 'POST',
        body: JSON.stringify(payload),
      }),
  });

  const isUnavailable = generateMutation.isError && generateMutation.error instanceof ApiError && generateMutation.error.status === 503;

  function handleGenerate() {
    generateMutation.mutate({ question, window: analysisWindow });
  }

  return (
    <Stack gap="xl">
      <div>
        <Title order={1} c={BRAND.textPrimary}>
          Insights
        </Title>
        <Text c={BRAND.textMuted} size="md">
          Faça uma pergunta livre. A resposta é montada a partir do pacote de dados da janela
          escolhida (os mesmos números da visão geral, com a confiança declarada) e do método
          (RAG), filtrado pelos grupos musculares que aparecem no período.
        </Text>
      </div>

      <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
        <Stack>
          <Group align="flex-end" wrap="wrap">
            <Select
              label="Janela de análise"
              value={analysisWindow}
              onChange={(value) => setAnalysisWindow(value || '28d')}
              data={windowOptions}
              w={180}
            />
          </Group>

          <Textarea
            label="Pergunta"
            minRows={3}
            value={question}
            onChange={(event) => setQuestion(event.currentTarget.value)}
          />

          <Button
            leftSection={<IconBrain size={16} />}
            color="brand"
            loading={generateMutation.isPending}
            disabled={!question.trim()}
            onClick={handleGenerate}
          >
            Gerar insight
          </Button>

          {generateMutation.isPending && (
            <Text c={BRAND.textMuted} size="sm">
              Consultando a IA — pode levar alguns segundos...
            </Text>
          )}
        </Stack>
      </Paper>

      {generateMutation.isError && (
        <Alert color="red" icon={<IconAlertTriangle />} title={isUnavailable ? 'IA indisponível no momento' : 'Erro ao gerar insight'}>
          {isUnavailable
            ? 'O backend não está com a integração de IA configurada. Tente novamente mais tarde ou avise um administrador.'
            : generateMutation.error instanceof Error
              ? generateMutation.error.message
              : 'Não foi possível gerar o insight.'}
        </Alert>
      )}

      {generateMutation.data && (
        <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
          <Title order={3} mb="sm" c={BRAND.textPrimary}>
            Resultado
          </Title>
          <AiMarkdown>{generateMutation.data.insight}</AiMarkdown>
        </Paper>
      )}
    </Stack>
  );
}
