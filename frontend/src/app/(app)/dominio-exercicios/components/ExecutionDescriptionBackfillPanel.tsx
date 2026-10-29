'use client';

import {
  Alert,
  Badge,
  Button,
  Group,
  List,
  Paper,
  Progress,
  Stack,
  Text,
  Tooltip,
} from '@mantine/core';
import { IconAlertTriangle, IconCheck, IconPlayerStop, IconSparkles } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useRef, useState } from 'react';
import { ApiError, apiFetch } from '@/lib/api';
import {
  progressLabel,
  runExecutionDescriptionBackfill,
  type BatchOutcome,
  type BatchProgress,
} from '@/lib/execution-description-batch';
import type {
  ExecutionDescriptionBatchResponse,
  ExecutionDescriptionPendingResponse,
} from '@/lib/exercise-catalog-admin-types';
import { BRAND } from '@/theme';

export const PENDING_QUERY_KEY = ['exercise-catalog-admin-execution-descriptions-pending'] as const;

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : error instanceof Error ? error.message : fallback;
}

/**
 * Backfill em lote das descrições de execução
 * (`POST /exercises/execution-descriptions/generate-batch`).
 *
 * Por que existe uma tela para isso: a rota já existia, mas só dava para
 * chamá-la à mão. Popular o catálogo em PRODUÇÃO não pode depender de alguém
 * com curl e um token de admin.
 *
 * Por que é um encadeamento e não um botão só: a rota tem teto de 10 itens e
 * um orçamento de tempo por chamada (cada item é uma chamada de LLM sob o
 * timeout de 29 s do API Gateway) e devolve `remaining`. Uma rodada parcial
 * por tempo é progresso normal. A regra de parada, o cancelamento e o acúmulo
 * de falhas moram em `lib/execution-description-batch.ts` — puros e testados;
 * aqui só mora a tela.
 *
 * Parar no meio é seguro por construção: o backend commita cada item gerado
 * na hora. Cancelar impede a PRÓXIMA chamada, nunca desfaz a anterior.
 *
 * Se mesmo assim o gateway cortar um request (504 sem CORS, que o `fetch`
 * reporta como status 0), o runner devolve `server_timeout`: o `onSettled`
 * revalida o contador de pendentes (o que foi gerado está salvo) e a tela
 * orienta a tentar de novo — em vez do texto genérico de "verifique sua
 * conexão", que aqui seria mentira.
 */
export function ExecutionDescriptionBackfillPanel({ isAdmin }: { isAdmin: boolean }) {
  const qc = useQueryClient();

  const pendingQuery = useQuery({
    queryKey: PENDING_QUERY_KEY,
    queryFn: () => apiFetch<ExecutionDescriptionPendingResponse>('/exercises/execution-descriptions/pending'),
  });

  const [progress, setProgress] = useState<BatchProgress | null>(null);
  const [outcome, setOutcome] = useState<BatchOutcome | null>(null);
  // `useRef` e não `useState`: o runner lê o cancelamento DENTRO do laço, e
  // um `state` congelaria no valor que existia quando a execução começou.
  const cancelled = useRef(false);

  const pending = pendingQuery.data?.pending ?? 0;
  const totalActive = pendingQuery.data?.total_active ?? 0;
  const maxBatch = pendingQuery.data?.max_batch ?? 10;

  const backfill = useMutation({
    mutationFn: async () => {
      cancelled.current = false;
      setOutcome(null);
      setProgress(null);
      const result = await runExecutionDescriptionBackfill({
        initialPending: pending,
        isCancelled: () => cancelled.current,
        // Status 0 = o `fetch` rejeitou (resposta sem CORS): em produção é o
        // API Gateway cortando o request em 29 s. Não é "sem conexão".
        isServerTimeout: (error) => error instanceof ApiError && error.status === 0,
        onProgress: setProgress,
        runBatch: () =>
          apiFetch<ExecutionDescriptionBatchResponse>('/exercises/execution-descriptions/generate-batch', {
            method: 'POST',
            // `limit` vem do próprio backend (`max_batch`): se o teto mudar
            // lá, a tela acompanha sozinha.
            // Sem `force`, e de propósito: esta tela SÓ preenche o que está
            // vazio. Regerar por cima (única coisa que pode apagar uma revisão
            // humana) continua sendo uma decisão exercício a exercício, no
            // botão "Regerar com IA" do drawer, com confirmação própria.
            body: JSON.stringify({ limit: maxBatch }),
          }),
      });
      return result;
    },
    onSettled: async () => {
      // Sempre revalida — inclusive depois de erro ou cancelamento, porque o
      // que já foi gerado ESTÁ gerado e o contador mudou.
      await qc.invalidateQueries({ queryKey: PENDING_QUERY_KEY });
      await qc.invalidateQueries({ queryKey: ['exercise-catalog-admin-exercises'] });
    },
    onSuccess: (result) => {
      setOutcome(result);
    },
  });

  const running = backfill.isPending;

  function startBackfill() {
    const aviso =
      `Gerar a descrição de execução de ${pending} exercício(s).

` +
      `Cada um é uma chamada de LLM paga, em lotes de ${maxBatch}. ` +
      `Nenhum texto existente ou revisado é sobrescrito. Confirma?`;
    if (!window.confirm(aviso)) return;
    backfill.mutate();
  }

  const percent = progress && progress.total > 0 ? Math.round((progress.processed / progress.total) * 100) : 0;

  return (
    <Paper withBorder radius="lg" p="lg" style={{ background: BRAND.bgElevated, borderColor: BRAND.border }}>
      <Group justify="space-between" align="flex-start" wrap="wrap" mb="sm">
        <Stack gap={4}>
          <Group gap="xs">
            <Text fw={600}>Descrições de execução</Text>
            {pendingQuery.isSuccess &&
              (pending === 0 ? (
                <Badge color="teal" variant="light" leftSection={<IconCheck size={12} />}>
                  catálogo completo
                </Badge>
              ) : (
                <Badge color="orange" variant="light">
                  {pending} sem descrição
                </Badge>
              ))}
          </Group>
          <Text size="sm" c={BRAND.textMuted}>
            {pendingQuery.isSuccess
              ? `${pending} de ${totalActive} exercício(s) ativo(s) ainda sem o texto que a tela de briefing mostra antes da primeira série.`
              : 'Carregando o estado do catálogo...'}
          </Text>
        </Stack>

        <Group gap="xs">
          {running && (
            <Button
              variant="default"
              leftSection={<IconPlayerStop size={16} />}
              onClick={() => {
                cancelled.current = true;
              }}
            >
              Parar
            </Button>
          )}
          <Tooltip label={isAdmin ? 'Gera em lote pela OpenAI' : 'Ação restrita a administradores'}>
            <Button
              color="brand"
              leftSection={<IconSparkles size={16} />}
              loading={running}
              disabled={!isAdmin || pending === 0 || !pendingQuery.isSuccess}
              onClick={startBackfill}
            >
              Gerar as que faltam
            </Button>
          </Tooltip>
        </Group>
      </Group>

      <Text size="xs" c={BRAND.textMuted}>
        Cada exercício é <strong>uma chamada de LLM paga</strong>, em lotes de até {maxBatch} (o servidor
        pode fechar um lote antes, por tempo, e a tela continua sozinha). O texto gerado
        <strong> não conta como revisado</strong>: entra como rascunho da IA e segue pendente de revisão até
        alguém abrir o exercício e salvá-lo. Este botão <strong>só preenche o que está vazio</strong> — nenhum
        texto existente, e muito menos um revisado por humano, é sobrescrito (para isso existe o
        &quot;Regerar com IA&quot; dentro de cada exercício). Parar no meio é seguro: o que já foi gerado fica
        gravado.
      </Text>

      {(running || progress) && (
        <Stack gap={4} mt="md">
          <Group justify="space-between">
            <Text size="sm">{progress ? progressLabel(progress) : 'iniciando...'}</Text>
            <Text size="sm" c={BRAND.textMuted}>
              {progress ? `${progress.generated} gerada(s) · ${progress.failed} falha(s)` : ''}
            </Text>
          </Group>
          <Progress value={percent} animated={running} color="brand" />
        </Stack>
      )}

      {pendingQuery.isError && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />} mt="md">
          {errorMessage(pendingQuery.error, 'Não foi possível contar os exercícios sem descrição.')}
        </Alert>
      )}

      {backfill.isError && (
        <Alert color="red" icon={<IconAlertTriangle size={16} />} mt="md">
          {errorMessage(backfill.error, 'A geração em lote falhou.')}
        </Alert>
      )}

      {outcome && !running && <BackfillOutcomeAlert outcome={outcome} />}
    </Paper>
  );
}

/** Resumo do fim da execução, com a lista do que falhou. */
function BackfillOutcomeAlert({ outcome }: { outcome: BatchOutcome }) {
  const resumo = `${outcome.generated} gerada(s), ${outcome.skipped} pulada(s), ${outcome.failed} falha(s) em ${outcome.rounds} lote(s).`;

  const porMotivo: Record<BatchOutcome['reason'], { color: string; titulo: string }> = {
    done: { color: 'teal', titulo: 'Backfill concluído' },
    cancelled: { color: 'yellow', titulo: 'Interrompido por você' },
    error: { color: 'red', titulo: 'Interrompido por um erro' },
    server_timeout: { color: 'yellow', titulo: 'Um lote demorou mais que o limite do servidor' },
    stalled: { color: 'yellow', titulo: 'Nada mais pôde ser gerado agora' },
  };
  const { color, titulo } = porMotivo[outcome.reason];

  return (
    <Alert color={color} mt="md" title={titulo}>
      <Stack gap="xs">
        <Text size="sm">{resumo}</Text>
        {outcome.reason === 'server_timeout' && (
          <Text size="sm">
            O servidor cortou a resposta de um lote por tempo. O que foi gerado até ali está salvo (o contador
            acima já foi atualizado); clique em &quot;Gerar as que faltam&quot; de novo para continuar.
          </Text>
        )}
        {outcome.reason !== 'done' && outcome.reason !== 'server_timeout' && outcome.remaining > 0 && (
          <Text size="sm">
            Ainda faltam {outcome.remaining}. O que já foi gerado está gravado — é só clicar em
            &quot;Gerar as que faltam&quot; de novo para continuar de onde parou.
          </Text>
        )}
        {outcome.reason === 'stalled' && (
          <Text size="xs" c={BRAND.textMuted}>
            O que sobrou está reservado por uma geração automática em andamento (disparada pelo briefing) ou
            vem falhando. Tente de novo em alguns minutos.
          </Text>
        )}
        {outcome.reason === 'error' && (
          <Text size="sm">{errorMessage(outcome.error, 'Erro desconhecido na chamada da rota.')}</Text>
        )}
        {outcome.failures.length > 0 && (
          <>
            <Text size="sm" fw={600}>
              Exercícios que falharam:
            </Text>
            <List size="xs">
              {outcome.failures.map((item) => (
                <List.Item key={item.exercise_id}>
                  #{item.exercise_id}
                  {item.error ? ` — ${item.error}` : ''}
                </List.Item>
              ))}
            </List>
          </>
        )}
      </Stack>
    </Alert>
  );
}
