// Helper GENÉRICO de polling do padrão 202 + job_id + polling em
// `GET /jobs/{job_id}` (ver `AsyncJob<T>` em `analytics-types.ts`), usado
// por qualquer endpoint que siga esse contrato assíncrono
// (`/cardio-recommendations/generate`, `/workout-plans/generate`,
// `/workouts-analysis/generate`, etc.).
//
// Generalizado a partir do PADRÃO já usado em dois lugares deste projeto,
// não copiado de nenhum dos dois sem ajuste:
//
//   - `fetchWorkoutAiResult`/`pollWorkoutAiJob` em `workout-mode-ai.ts`:
//     mesma forma (dispara o POST, resolve o job_id, faz polling e devolve
//     o `result` com o shape do endpoint síncrono antigo), mas com
//     intervalo agressivo de 500ms e teto de 20s — específico do modo
//     treino (ao vivo, turnos de IA curtos), não generalizado aqui.
//   - `agente-cardio/page.tsx`/`GenerateDraftModal.tsx` (planos-treino):
//     usam `useQuery` com `refetchInterval` de 2s enquanto `status ===
//     'pending'`, sem teto client-side explícito — o padrão de INTERVALO
//     (2s) que este helper adota como default, mas hoje sem cap.
//
// Este helper soma os dois: intervalo de 2s (consistente com
// agente-cardio/planos-treino) + teto de espera configurável (default 90s,
// pedido explicitamente para `/workouts-analysis/generate` em `/treinos`,
// bem mais generoso que os 20s do modo treino porque o prompt de análise de
// treinos injeta bem mais contexto). Devolve uma Promise simples (em vez de
// um hook `useQuery`) para caber num `useMutation.mutationFn` imperativo,
// disparado por um clique em botão — não um polling reativo a estado de UI.
import { apiFetch } from './api';
import type { AsyncJob } from './analytics-types';

export const ASYNC_JOB_POLL_INTERVAL_MS = 2000;
export const ASYNC_JOB_POLL_TIMEOUT_MS = 90000;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export type PollAsyncJobOptions = {
  intervalMs?: number;
  timeoutMs?: number;
  // Textos de erro. O default fala de "análise" porque foi daí que o helper
  // nasceu (`/workouts-analysis/generate`); qualquer outro consumidor passa os
  // seus (ex.: a periodização em `/modelos-treino`, cujo job pode falhar por
  // configuração do backend e não por "análise" nenhuma).
  failedFallbackMessage?: string;
  timeoutMessage?: string;
};

/**
 * Faz polling de `GET /jobs/{jobId}` até o job terminar (`done`/`failed`)
 * ou o teto de espera ser atingido, devolvendo `job.result` no primeiro
 * caso. Lança `Error` com mensagem amigável em caso de falha ou timeout —
 * quem chama decide como exibir (nunca derruba o resto da tela).
 */
export async function pollAsyncJob<TResult>(jobId: string, options: PollAsyncJobOptions = {}): Promise<TResult> {
  const intervalMs = options.intervalMs ?? ASYNC_JOB_POLL_INTERVAL_MS;
  const timeoutMs = options.timeoutMs ?? ASYNC_JOB_POLL_TIMEOUT_MS;
  const deadline = Date.now() + timeoutMs;

  // eslint-disable-next-line no-constant-condition
  while (true) {
    const job = await apiFetch<AsyncJob<TResult>>(`/jobs/${jobId}`);
    if (job.status === 'done') {
      return job.result as TResult;
    }
    if (job.status === 'failed') {
      throw new Error(job.error || options.failedFallbackMessage || 'Falha ao gerar a análise. Tente novamente.');
    }
    if (Date.now() >= deadline) {
      throw new Error(
        options.timeoutMessage || 'Tempo esgotado aguardando a análise da IA. Tente novamente em instantes.',
      );
    }
    await sleep(intervalMs);
  }
}

/**
 * Dispara um `POST` assíncrono (responde 202 `{ job_id, status }`) e resolve
 * com o `result` final via `pollAsyncJob` — uma chamada só, pronta para um
 * `useMutation.mutationFn`.
 */
export async function dispatchAndPollJob<TResult>(
  path: string,
  init: RequestInit = {},
  options?: PollAsyncJobOptions,
): Promise<TResult> {
  const { job_id: jobId } = await apiFetch<{ job_id: string; status: string }>(path, {
    ...init,
    method: init.method ?? 'POST',
  });
  return pollAsyncJob<TResult>(jobId, options);
}
