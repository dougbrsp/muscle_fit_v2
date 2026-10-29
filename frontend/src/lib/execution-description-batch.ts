// Encadeamento do backfill das descrições de execução (M4). Lógica PURA:
// sem React e sem fetch — a chamada HTTP entra por injeção (`runBatch`), que
// é o que deixa o vitest (`environment: node`) provar as regras de parada, o
// cancelamento e o acúmulo de falhas sem subir navegador nem bater na OpenAI.
//
// Por que existe um "runner" e não um simples `for`: a rota
// `POST /exercises/execution-descriptions/generate-batch` tem teto de 10 itens
// E um orçamento de tempo por chamada (cada item é uma chamada de LLM sob o
// timeout de 29 s do API Gateway) e devolve `remaining`. Popular um catálogo
// de ~centenas de exercícios é, portanto, uma SEQUÊNCIA de chamadas — e uma
// sequência que o admin precisa poder acompanhar e interromper sem estragar
// nada.
//
// Uma rodada que volta PARCIAL por tempo (`time_budget_exhausted: true`,
// `requested` menor que o lote) é progresso normal, não erro: o backend
// gravou o que gerou e a próxima chamada continua de onde parou.
//
// Garantia de segurança: parar no meio nunca corrompe. Cada item gerado é
// commitado no backend na hora; o cancelamento só impede a PRÓXIMA chamada.
// Nada é desfeito, nada fica pela metade.

import type { ExecutionDescriptionBatchResponse } from './exercise-catalog-admin-types';

export type BatchItemResult = ExecutionDescriptionBatchResponse['items'][number];

export type BatchProgress = {
  /** Quantos itens o backfill já processou nesta execução (gerados + pulados + falhos). */
  processed: number;
  generated: number;
  skipped: number;
  failed: number;
  /** Quantos o servidor diz que ainda faltam, depois da última rodada. */
  remaining: number;
  /** Total estimado = já processado + o que falta. É o denominador de "32 de 86". */
  total: number;
  /** Quantas chamadas à rota foram feitas. */
  rounds: number;
  /** Quantas dessas rodadas voltaram parciais por orçamento de tempo do servidor. */
  partialRounds: number;
};

export type BatchStopReason =
  /** `remaining` chegou a zero: acabou de verdade. */
  | 'done'
  /** O admin pediu para parar. O que já foi gerado, está gerado. */
  | 'cancelled'
  /** A rota falhou (502, 403...). O erro vem em `error`. */
  | 'error'
  /**
   * A chamada morreu sem resposta utilizável (o `fetch` rejeitou: status 0).
   * Em produção isso é o API Gateway cortando o request em 29 s e devolvendo
   * um 504 sem CORS — o lote demorou mais que o limite do servidor. O que o
   * backend gerou até ali ESTÁ gravado (commit por item); só a resposta se
   * perdeu. É um caso distinto de `error` porque a orientação ao admin é
   * outra: revalidar o pendente e tentar de novo, não "verificar a conexão".
   */
  | 'server_timeout'
  /**
   * A rodada não processou NENHUM item embora o servidor ainda diga que
   * faltam. Acontece quando o que sobrou está reservado por uma geração
   * automática em andamento, ou falha sempre. Sem esta parada, o encadeamento
   * viraria um laço infinito de chamadas que não geram nada.
   */
  | 'stalled';

export type BatchOutcome = BatchProgress & {
  reason: BatchStopReason;
  /** Só os itens que falharam, acumulados de todas as rodadas. */
  failures: BatchItemResult[];
  error?: unknown;
};

export type RunBackfillOptions = {
  /** Faz UMA chamada à rota e devolve a resposta. Injetado pelo componente. */
  runBatch: () => Promise<ExecutionDescriptionBatchResponse>;
  /** Quantos faltavam ANTES de começar (`GET .../pending`), só para o denominador. */
  initialPending: number;
  /** Chamado depois de cada rodada, para a barra de progresso. */
  onProgress?: (progress: BatchProgress) => void;
  /** `true` interrompe antes da PRÓXIMA chamada. Nunca no meio de uma. */
  isCancelled?: () => boolean;
  /**
   * Diz se um erro lançado por `runBatch` é "o servidor não respondeu" (fetch
   * rejeitado, status 0) — vira `reason: 'server_timeout'`. Injetado para a
   * lib não conhecer `ApiError`. Sem isso, todo erro é `'error'`.
   */
  isServerTimeout?: (error: unknown) => boolean;
  /** Trava de segurança contra laço infinito por resposta inesperada. */
  maxRounds?: number;
};

// Teto duro de chamadas numa execução. Com o lote de 10 do backend, 200
// rodadas cobrem 2000 exercícios — ordens de grandeza acima do catálogo real
// — e ainda assim garantem que um servidor devolvendo `remaining` errado não
// deixe a tela chamando a rota para sempre.
export const MAX_ROUNDS = 200;

function progressFrom(state: BatchProgress): BatchProgress {
  return { ...state, total: state.processed + state.remaining };
}

/**
 * Encadeia as chamadas do backfill até `remaining` chegar a zero.
 *
 * Nunca lança: uma falha de rede vira `reason: 'error'` com o progresso
 * parcial preservado, porque o que já foi gerado continua gravado no catálogo
 * e o admin precisa ver isso.
 */
export async function runExecutionDescriptionBackfill(
  options: RunBackfillOptions,
): Promise<BatchOutcome> {
  const {
    runBatch,
    initialPending,
    onProgress,
    isCancelled,
    isServerTimeout,
    maxRounds = MAX_ROUNDS,
  } = options;

  const state: BatchProgress = {
    processed: 0,
    generated: 0,
    skipped: 0,
    failed: 0,
    remaining: Math.max(0, initialPending),
    total: Math.max(0, initialPending),
    rounds: 0,
    partialRounds: 0,
  };
  const failures: BatchItemResult[] = [];

  const finish = (reason: BatchStopReason, error?: unknown): BatchOutcome => ({
    ...progressFrom(state),
    reason,
    failures,
    ...(error === undefined ? {} : { error }),
  });

  if (state.remaining <= 0) return finish('done');

  while (state.rounds < maxRounds) {
    if (isCancelled?.()) return finish('cancelled');

    let response: ExecutionDescriptionBatchResponse;
    try {
      response = await runBatch();
    } catch (error) {
      return finish(isServerTimeout?.(error) ? 'server_timeout' : 'error', error);
    }

    state.rounds += 1;
    if (response.time_budget_exhausted) state.partialRounds += 1;
    state.generated += response.generated || 0;
    state.skipped += response.skipped || 0;
    state.failed += response.failed || 0;
    // `requested` é o que o servidor efetivamente TENTOU — numa rodada parcial
    // por tempo, é menor que o lote pedido, e o que sobrou continua em
    // `remaining`. Somar assim mantém "processado + faltante" honesto.
    state.processed += response.requested || 0;
    state.remaining = Math.max(0, response.remaining || 0);
    failures.push(...(response.items || []).filter((item) => item.status === 'failed'));

    onProgress?.(progressFrom(state));

    if (state.remaining <= 0) return finish('done');
    // Servidor ainda diz que falta, mas esta rodada não pegou nada: insistir
    // só repetiria a mesma resposta vazia.
    if (!response.requested) return finish('stalled');
  }

  return finish('stalled');
}

/** Texto do progresso ("32 de 86"), com o denominador nunca menor que o numerador. */
export function progressLabel(progress: BatchProgress): string {
  return `${progress.processed} de ${Math.max(progress.total, progress.processed)}`;
}
