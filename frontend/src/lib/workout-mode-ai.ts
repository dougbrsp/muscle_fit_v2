// Chamadas de IA assíncronas do Modo Treino (202 + `job_id` + polling em
// `GET /jobs/{job_id}`). Os tipos de resultado (`SetEvaluationResult`,
// `ExerciseSummaryResult`, `WorkoutAiSummaryResult`) ficam em
// `workout-mode-types.ts`; aqui só o que usa `apiFetch`. Separado no PR B da
// Evolução ZEPP para o núcleo puro do Modo Treino não depender de rede.
// Comportamento idêntico ao de antes do split.

import { apiFetch } from './api';
import type { AsyncJob } from './analytics-types';

// Polling AGRESSIVO de curto intervalo (500ms), não long-polling — decisão
// deliberada para o modo treino, que é ao vivo (latência importa mais aqui
// do que em `/agente-cardio`/`/planos-treino`, que usam 2s, ver
// `AgenteCardioPage`/`GenerateDraftModal`):
// - As 3 chamadas daqui são UM turno de OpenAI cada, tipicamente concluído
//   em 1-3s — bem mais curto que geração de plano/cardio (que injeta bem
//   mais contexto no prompt). Um intervalo de 500ms sente "quase instantâneo"
//   sem sobrecarregar nada: no pior caso são poucas dezenas de GETs curtos
//   contra `GET /jobs/{id}` (leitura simples de item no DynamoDB via Lambda),
//   custo desprezível neste volume (uso pessoal/poucos usuários).
// - Long-polling (a Lambda de `GET /jobs/{id}` ficar bloqueada esperando o
//   job concluir antes de responder) foi CONSIDERADO E DESCARTADO: exigiria
//   reescrever `app/jobs.py` (hoje uma leitura simples e síncrona) para um
//   loop de poll interno ao DynamoDB dentro do handler, aumentando a duração
//   faturada da Lambda por requisição e a complexidade do endpoint mais
//   usado do padrão assíncrono — sem ganho perceptível de latência real
//   neste volume/escala (a resposta já chega rápido com poll curto), então
//   o trade-off não compensa aqui. Fica como opção a revisitar só se a
//   concorrência/volume crescerem a ponto do custo de poll curto pesar.
const WORKOUT_AI_POLL_INTERVAL_MS = 500;
// Teto de espera no cliente — bem abaixo do timeout de 60s da Lambda
// `musclefit-workout-ai` (ver `infra/lambda_workout_ai.tf`), generoso o
// bastante para uma chamada de IA lenta sem deixar o usuário esperando
// indefinidamente por um card "avaliação indisponível"/"resumo indisponível".
const WORKOUT_AI_POLL_TIMEOUT_MS = 20000;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function pollWorkoutAiJob<TResult>(jobId: string): Promise<TResult> {
  const deadline = Date.now() + WORKOUT_AI_POLL_TIMEOUT_MS;
  // eslint-disable-next-line no-constant-condition
  while (true) {
    const job = await apiFetch<AsyncJob<TResult>>(`/jobs/${jobId}`);
    if (job.status === 'done') {
      return job.result as TResult;
    }
    if (job.status === 'failed') {
      throw new Error(job.error || 'Falha ao gerar resposta de IA.');
    }
    if (Date.now() >= deadline) {
      throw new Error('Tempo esgotado aguardando resposta de IA.');
    }
    await sleep(WORKOUT_AI_POLL_INTERVAL_MS);
  }
}

// Dispara um dos 3 `POST` assíncronos de IA do modo treino (retornam 202 +
// `job_id`) e resolve/rejeita com o MESMO shape de resultado do endpoint
// síncrono antigo, via polling curto em `GET /jobs/{job_id}` — usar no lugar
// de `apiFetch` direto nesses 3 endpoints específicos.
export async function fetchWorkoutAiResult<TResult>(path: string, init: RequestInit = {}): Promise<TResult> {
  const { job_id } = await apiFetch<{ job_id: string; status: string }>(path, { ...init, method: init.method ?? 'POST' });
  return pollWorkoutAiJob<TResult>(job_id);
}
