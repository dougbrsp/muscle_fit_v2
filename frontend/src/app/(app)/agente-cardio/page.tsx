'use client';

import dynamic from 'next/dynamic';
import { ChartPlaceholder } from '@/components/charts/ChartPlaceholder';
import {
  ActionIcon,
  Accordion,
  Alert,
  Badge,
  Button,
  Group,
  Loader,
  Modal,
  Pagination,
  Paper,
  Progress,
  Select,
  Stack,
  Table,
  Text,
  Title,
  Tooltip,
} from '@mantine/core';
import { useDisclosure } from '@mantine/hooks';
import { IconAlertTriangle, IconMoonStars, IconRefresh, IconSparkles, IconTrash } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { preloadMarkdown } from '@/components/LazyMarkdown';
import { CardioRecommendationPanel } from '@/components/cardio/CardioRecommendationPanel';
import { apiFetch } from '@/lib/api';
import type {
  AsyncJob,
  CardioAgentContextResponse,
  CardioGenerationResult,
  CardioRecommendation,
  CardioRecommendationsResponse,
} from '@/lib/analytics-types';
import { readinessColor, readinessIcon, readinessLabel } from '@/lib/muscle-readiness';
import { statusVisual } from '@/lib/dashboard-status';
import { BRAND } from '@/theme';
import { formatDateOrDash, formatDateTimeOrDash } from '@/lib/format';

// COD-14: recharts sob demanda; o placeholder tem a altura do gráfico.
const WellnessChart = dynamic(() => import('./AgenteCardioCharts').then((m) => m.WellnessChart), {
  ssr: false,
  loading: () => <ChartPlaceholder height={260} />,
});
const SleepChart = dynamic(() => import('./AgenteCardioCharts').then((m) => m.SleepChart), {
  ssr: false,
  loading: () => <ChartPlaceholder height={220} />,
});
const EffortChart = dynamic(() => import('./AgenteCardioCharts').then((m) => m.EffortChart), {
  ssr: false,
  loading: () => <ChartPlaceholder height={220} />,
});

const dayOptions = [
  { value: '7', label: '7 dias' },
  { value: '14', label: '14 dias' },
  { value: '30', label: '30 dias' },
];

// Mesmo tamanho de página usado em /registro-exercicios para paginação
// client-side de listas já carregadas por inteiro do backend.
const HISTORY_PAGE_SIZE = 20;

// Mesma cópia que existia em /recuperacao-muscular — agora do ponto único.
const colorForStatus = (status: string) => statusVisual('recoveryStatus', status).color;

function fmt(v: number | null | undefined, decimals = 1): string {
  return v != null && Number.isFinite(Number(v)) ? Number(v).toFixed(decimals) : '—';
}

// Visual desta tela para o markdown vindo da IA (Título/Texto/Lista Mantine);
// links, imagens e HTML seguem a política única de SafeMarkdown (IA-15).
const markdownComponents = {
  h1: ({ children }: { children?: ReactNode }) => (
    <Title order={3} mt="md" mb="xs" c={BRAND.textPrimary}>
      {children}
    </Title>
  ),
  h2: ({ children }: { children?: ReactNode }) => (
    <Title order={4} mt="md" mb="xs" c={BRAND.textPrimary}>
      {children}
    </Title>
  ),
  h3: ({ children }: { children?: ReactNode }) => (
    <Title order={5} mt="sm" mb="xs" c={BRAND.textPrimary}>
      {children}
    </Title>
  ),
  p: ({ children }: { children?: ReactNode }) => (
    <Text mb="xs" c={BRAND.textPrimary} style={{ whiteSpace: 'pre-wrap' }}>
      {children}
    </Text>
  ),
  strong: ({ children }: { children?: ReactNode }) => (
    <Text component="strong" fw={700} c={BRAND.textPrimary}>
      {children}
    </Text>
  ),
  ul: ({ children }: { children?: ReactNode }) => (
    <Text component="ul" mb="xs" c={BRAND.textPrimary} style={{ paddingLeft: 20 }}>
      {children}
    </Text>
  ),
  ol: ({ children }: { children?: ReactNode }) => (
    <Text component="ol" mb="xs" c={BRAND.textPrimary} style={{ paddingLeft: 20 }}>
      {children}
    </Text>
  ),
  li: ({ children }: { children?: ReactNode }) => <li>{children}</li>,
};

export default function AgenteCardioPage() {
  // COD-14: o Markdown da IA vem sob demanda; começa a baixar já na montagem.
  useEffect(preloadMarkdown, []);
  const qc = useQueryClient();
  const [days, setDays] = useState('14');
  const [jobId, setJobId] = useState<string | null>(null);
  const [historyPage, setHistoryPage] = useState(1);
  const [deleteTarget, setDeleteTarget] = useState<CardioRecommendation | null>(null);
  const [deleteOpened, { open: openDelete, close: closeDelete }] = useDisclosure(false);

  const historyQuery = useQuery({
    queryKey: ['cardio-recommendations-history'],
    // 100 = teto do backend (LIST_MAX_LIMIT em app/cardio_recommendations.py,
    // limite de 1 MiB da RDS Data API); valores maiores são truncados.
    queryFn: () => apiFetch<CardioRecommendationsResponse>('/cardio-recommendations?limit=100'),
  });

  const contextQuery = useQuery({
    queryKey: ['cardio-agent-context', days],
    queryFn: () => apiFetch<CardioAgentContextResponse>(`/analytics/cardio-agent-context?days=${days}`),
  });

  const generateMutation = useMutation({
    mutationFn: (payload: { days: number }) =>
      apiFetch<{ job_id: string; status: string }>('/cardio-recommendations/generate', {
        method: 'POST',
        body: JSON.stringify(payload),
      }),
    onSuccess: (data) => {
      setJobId(data.job_id);
    },
  });

  // Padrão 202 + job_id + polling: a geração roda em uma Lambda assíncrona
  // (`musclefit-cardio-agent`), então aqui só consultamos o estado do job em
  // GET /jobs/{job_id} a cada 2s enquanto ele estiver "pending".
  const jobQuery = useQuery({
    queryKey: ['cardio-job', jobId],
    queryFn: () => apiFetch<AsyncJob<CardioGenerationResult>>(`/jobs/${jobId}`),
    enabled: !!jobId,
    refetchInterval: (query) => (query.state.data?.status === 'pending' ? 2000 : false),
  });

  const job = jobQuery.data;
  const isGenerating = generateMutation.isPending || (!!jobId && (job?.status === 'pending' || jobQuery.isLoading));
  const generatedResult = job?.status === 'done' ? job.result : undefined;

  useEffect(() => {
    // Job concluído: o histórico (GET /cardio-recommendations) precisa
    // refletir a nova linha imediatamente, sem esperar o próximo foco/refetch
    // manual da tabela abaixo.
    if (job?.status === 'done') {
      qc.invalidateQueries({ queryKey: ['cardio-recommendations-history'] });
    }
  }, [job?.status, jobId, qc]);

  function handleGenerate() {
    setJobId(null);
    generateMutation.mutate({ days: Number(days) });
  }

  function handleOpenDelete(rec: CardioRecommendation) {
    setDeleteTarget(rec);
    openDelete();
  }

  const deleteMutation = useMutation({
    mutationFn: (id: number) => apiFetch(`/cardio-recommendations/${id}`, { method: 'DELETE' }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['cardio-recommendations-history'] });
      closeDelete();
      setDeleteTarget(null);
    },
  });

  const items = historyQuery.data?.items || [];

  const ctx = contextQuery.data;

  const wellnessChartData = useMemo(
    () =>
      [...(ctx?.intervals_wellness || [])]
        .reverse()
        .map((item) => ({
          date: item.wellness_date,
          fitness_ctl: item.fitness_ctl != null ? Number(item.fitness_ctl) : null,
          fatigue_atl: item.fatigue_atl != null ? Number(item.fatigue_atl) : null,
          form_tsb: item.form_tsb != null ? Number(item.form_tsb) : null,
        })),
    [ctx],
  );

  // Item mais recente (a query já vem ordenada DESC por wellness_date, então
  // pegamos antes do reverse acima) — usado para exibir rampa e PAI ao lado
  // do título do gráfico, como no onpremise.
  const latestWellness = ctx?.intervals_wellness?.[0];

  const sleepChartData = useMemo(
    () =>
      [...(ctx?.sleep_metrics || [])]
        .reverse()
        .map((item) => ({
          date: item.local_date,
          duration_hours: item.duration_hours != null ? Number(item.duration_hours) : null,
          deep_sleep_hours: item.deep_sleep_hours != null ? Number(item.deep_sleep_hours) : null,
        })),
    [ctx],
  );

  const effortChartData = useMemo(
    () =>
      [...(ctx?.effort_metrics_estimated || [])]
        .reverse()
        .map((item) => ({
          date: item.local_date,
          epoc_7d_estimated: item.epoc_7d_estimated,
          pai_7d_estimated: item.pai_7d_estimated,
        })),
    [ctx],
  );

  // `muscle_recovery` do contexto de cardio é SEMPRE 14 dias fixos no
  // backend (CardioAgentContextService.build_context), independente da
  // janela `days` selecionada na tela — por isso o rótulo abaixo não usa a
  // variável `days`.
  const recoveryItems = ctx?.muscle_recovery || [];

  const historyTotalPages = Math.max(1, Math.ceil(items.length / HISTORY_PAGE_SIZE));
  const paginatedHistory = useMemo(() => {
    const start = (historyPage - 1) * HISTORY_PAGE_SIZE;
    return items.slice(start, start + HISTORY_PAGE_SIZE);
  }, [items, historyPage]);

  useEffect(() => {
    // Se o histórico encolher (ex.: após uma exclusão) e a página atual
    // deixar de existir, volta para a última página válida.
    setHistoryPage((current) => Math.min(current, historyTotalPages));
  }, [historyTotalPages]);

  return (
    <Stack gap="xl">
      <Group justify="space-between" align="flex-end" wrap="wrap">
        <div>
          <Title order={1} c={BRAND.textPrimary}>
            Agente de Cardio
          </Title>
          <Text c={BRAND.textMuted} size="md">
            Gera uma recomendação de cardio com base no seu contexto recente (recuperação, sono, esforço e carga).
          </Text>
        </div>
        <Group align="flex-end">
          <Select label="Janela de contexto" value={days} onChange={(value) => setDays(value || '14')} data={dayOptions} w={170} />
          <Button
            leftSection={<IconSparkles size={16} />}
            color="brand"
            loading={isGenerating}
            onClick={handleGenerate}
          >
            Gerar recomendação
          </Button>
        </Group>
      </Group>

      {contextQuery.isLoading && (
        <Group>
          <Loader size="sm" color="brand" />
          <Text c={BRAND.textMuted}>Carregando contexto...</Text>
        </Group>
      )}

      {contextQuery.isError && (
        <Alert color="red" icon={<IconAlertTriangle />} title="Erro ao carregar contexto">
          {contextQuery.error instanceof Error ? contextQuery.error.message : 'Não foi possível consultar a Fitness API.'}
        </Alert>
      )}

      {/* ── Carga cardiovascular (Intervals.icu) ── */}
      <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
        <Group justify="space-between" mb="sm" wrap="wrap">
          <Title order={3} c={BRAND.textPrimary}>
            Carga cardiovascular (Intervals.icu) — {days} dias
          </Title>
          {latestWellness && (
            <Group gap="md">
              <Text size="xs" c={BRAND.textMuted}>
                Rampa: <strong>{fmt(latestWellness.ramp_rate)}</strong>
              </Text>
              <Text size="xs" c={BRAND.textMuted}>
                PAI 7 dias (estimado): <strong>{fmt(ctx?.effort_metrics_estimated?.[0]?.pai_7d_estimated, 0)}</strong>
              </Text>
            </Group>
          )}
        </Group>
        {wellnessChartData.length > 0 ? (
          <WellnessChart wellnessChartData={wellnessChartData} />
        ) : (
          <Text c={BRAND.textMuted} size="sm">
            Sem dados de Intervals.icu no período — conecte sua conta em Intervals.icu para ver esta seção.
          </Text>
        )}
      </Paper>

      <Group grow align="stretch" wrap="wrap">
        {/* ── Sono ── */}
        <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border, minWidth: 280 }}>
          <Group gap={6} mb="sm">
            <IconMoonStars size={16} color={BRAND.orange} />
            <Title order={4} c={BRAND.textPrimary}>
              Sono — {days} dias
            </Title>
          </Group>
          {sleepChartData.length > 0 ? (
            <SleepChart sleepChartData={sleepChartData} />
          ) : (
            <Text c={BRAND.textMuted} size="sm">
              Sem registros manuais de sono no período — cadastre em Indicadores físicos.
            </Text>
          )}
        </Paper>

        {/* ── Esforço (EPOC/PAI de 7 dias, estimado) ── */}
        <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border, minWidth: 280 }}>
          <Title order={4} mb="sm" c={BRAND.textPrimary}>
            Esforço (EPOC/PAI de 7 dias, estimado) — {days} dias
          </Title>
          {effortChartData.length > 0 ? (
            <EffortChart effortChartData={effortChartData} />
          ) : (
            <Text c={BRAND.textMuted} size="sm">
              Sem atividades do Intervals.icu com tempo em zona de FC no período.
            </Text>
          )}
        </Paper>
      </Group>

      {/* ── Recuperação muscular (14 dias, fixo) ── */}
      <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
        <Title order={4} mb="sm" c={BRAND.textPrimary}>
          Recuperação muscular (14 dias)
        </Title>
        {recoveryItems.length > 0 ? (
          <Table.ScrollContainer minWidth={600}>
            <Table striped highlightOnHover withTableBorder fz="sm">
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>Grupo muscular</Table.Th>
                  <Table.Th>Recuperação</Table.Th>
                  <Table.Th>Prontidão</Table.Th>
                  <Table.Th>Última sessão</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {recoveryItems.map((item) => (
                  <Table.Tr key={item.muscle_group}>
                    <Table.Td>{item.muscle_group}</Table.Td>
                    <Table.Td>
                      <Group gap={6} wrap="nowrap">
                        <Progress value={item.recovery_pct} color={colorForStatus(item.status)} radius="xl" w={90} />
                        <Text size="xs" c={BRAND.textPrimary}>
                          {fmt(item.recovery_pct, 0)}%
                        </Text>
                      </Group>
                    </Table.Td>
                    <Table.Td>
                      {(() => {
                        const ReadinessIcon = readinessIcon(item.readiness_status);
                        return (
                          <Tooltip label={readinessLabel(item.readiness_status)} withArrow>
                            <ReadinessIcon
                              size={20}
                              color={`var(--mantine-color-${readinessColor(item.readiness_status)}-6)`}
                              style={{ cursor: 'help' }}
                            />
                          </Tooltip>
                        );
                      })()}
                    </Table.Td>
                    <Table.Td>{formatDateOrDash(item.last_trained_at)}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </Table.ScrollContainer>
        ) : (
          <Text c={BRAND.textMuted} size="sm">
            Sem dados de recuperação muscular nos últimos 14 dias.
          </Text>
        )}
      </Paper>

      {generateMutation.isError && (
        <Alert color="red" icon={<IconAlertTriangle />} title="Erro ao iniciar a geração">
          {generateMutation.error instanceof Error ? generateMutation.error.message : 'Não foi possível acionar o Agente de Cardio.'}
        </Alert>
      )}

      {job?.status === 'failed' && (
        <Alert color="red" icon={<IconAlertTriangle />} title="A geração falhou">
          {job.error || 'O Agente de Cardio não conseguiu gerar a recomendação. Tente novamente.'}
        </Alert>
      )}

      {isGenerating && (
        <Group>
          <Loader size="sm" color="brand" />
          <Text c={BRAND.textMuted}>Gerando recomendação com o Agente de Cardio...</Text>
        </Group>
      )}

      {generatedResult && (
        <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
          <Group justify="space-between" mb="sm">
            <Title order={3} c={BRAND.textPrimary}>
              Recomendação gerada
            </Title>
            <Badge color="brand" variant="light">
              {formatDateOrDash(generatedResult.reference_date)}
            </Badge>
          </Group>
          <CardioRecommendationPanel
            recommendationId={generatedResult.id}
            markdown={generatedResult.recommendation_markdown}
            promptVersion={generatedResult.prompt_version}
            markdownComponents={markdownComponents}
          />
        </Paper>
      )}

      <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
        <Group justify="space-between" mb="sm">
          <Title order={3} c={BRAND.textPrimary}>
            Histórico de recomendações
          </Title>
          <Button
            variant="subtle"
            color="brand"
            size="xs"
            leftSection={<IconRefresh size={14} />}
            onClick={() => historyQuery.refetch()}
          >
            Atualizar
          </Button>
        </Group>

        {historyQuery.isLoading && (
          <Group>
            <Loader size="sm" color="brand" />
            <Text c={BRAND.textMuted}>Carregando histórico...</Text>
          </Group>
        )}

        {historyQuery.isError && (
          <Alert color="red" icon={<IconAlertTriangle />} title="Erro ao carregar histórico">
            {historyQuery.error instanceof Error ? historyQuery.error.message : 'Não foi possível consultar a Fitness API.'}
          </Alert>
        )}

        {!historyQuery.isLoading && !historyQuery.isError && items.length === 0 && (
          <Text c={BRAND.textMuted} size="sm">
            Nenhuma recomendação gerada ainda.
          </Text>
        )}

        {!historyQuery.isLoading && !historyQuery.isError && paginatedHistory.length > 0 && (
          <Accordion variant="separated" radius="md">
            {paginatedHistory.map((rec) => (
              <Accordion.Item key={rec.id} value={String(rec.id)}>
                <Accordion.Control>
                  <Group justify="space-between" wrap="wrap" pr="xs">
                    <Text fw={600} c={BRAND.textPrimary}>
                      {formatDateOrDash(rec.reference_date)}
                    </Text>
                    <Group gap="xs" wrap="nowrap">
                      <Text size="xs" c={BRAND.textMuted}>
                        Gerado em {formatDateTimeOrDash(rec.generated_at)}
                        {rec.model ? ` · ${rec.model}` : ''}
                      </Text>
                      <Tooltip label="Excluir">
                        <ActionIcon
                          variant="subtle"
                          color="red"
                          onClick={(event) => {
                            event.stopPropagation();
                            handleOpenDelete(rec);
                          }}
                          aria-label="Excluir recomendação"
                        >
                          <IconTrash size={16} />
                        </ActionIcon>
                      </Tooltip>
                    </Group>
                  </Group>
                </Accordion.Control>
                <Accordion.Panel>
                  <CardioRecommendationPanel
                    recommendationId={rec.id}
                    markdown={rec.recommendation_markdown}
                    promptVersion={rec.prompt_version}
                    markdownComponents={markdownComponents}
                  />
                </Accordion.Panel>
              </Accordion.Item>
            ))}
          </Accordion>
        )}

        {historyTotalPages > 1 && (
          <Group justify="center" mt="md">
            <Pagination total={historyTotalPages} value={historyPage} onChange={setHistoryPage} color="brand" />
          </Group>
        )}
      </Paper>

      {/* Confirmação de exclusão: cardio_recommendations é log de auditoria
          imutável (sem PUT/UNDO) — o modal exige um clique explícito no
          botão "Excluir" antes de disparar o DELETE, nunca um confirm()
          nativo do navegador. */}
      <Modal opened={deleteOpened} onClose={closeDelete} title="Confirmar exclusão" centered>
        <Stack gap="md">
          <Text c={BRAND.textPrimary}>
            Excluir a recomendação de{' '}
            <Text component="span" fw={700}>
              {deleteTarget ? formatDateOrDash(deleteTarget.reference_date) : ''}
            </Text>
            ? Esta ação não pode ser desfeita — o histórico é um log de auditoria imutável.
          </Text>

          {deleteMutation.isError && (
            <Alert color="red" icon={<IconAlertTriangle />} title="Erro ao excluir">
              {deleteMutation.error instanceof Error ? deleteMutation.error.message : 'Não foi possível excluir o registro.'}
            </Alert>
          )}

          <Group justify="flex-end">
            <Button variant="default" onClick={closeDelete} disabled={deleteMutation.isPending}>
              Cancelar
            </Button>
            <Button
              color="red"
              loading={deleteMutation.isPending}
              onClick={() => deleteTarget && deleteMutation.mutate(deleteTarget.id)}
            >
              Excluir
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
