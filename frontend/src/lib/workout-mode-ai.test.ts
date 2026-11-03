import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// Prazo de polling do Modo Treino: o padrao de 20s e preservado; um prazo
// proprio (resumo do treino) segue consultando depois dos 20s.
const apiFetch = vi.fn();
vi.mock('./api', async (importOriginal) => ({ ...(await importOriginal<typeof import('./api')>()), apiFetch }));

const { fetchWorkoutAiResult, WORKOUT_AI_POLL_TIMEOUT_MS } = await import('./workout-mode-ai');

// Job que vira `done` apos `doneAfterMs` (null = nunca).
function jobs(doneAfterMs: number | null) {
  const inicio = Date.now();
  apiFetch.mockImplementation(async (path: string) => {
    if (path.startsWith('/jobs/')) {
      if (doneAfterMs !== null && Date.now() - inicio >= doneAfterMs) {
        return { status: 'done', result: { summary: 'ok' } };
      }
      return { status: 'pending' };
    }
    return { job_id: 'j1', status: 'pending' };
  });
}

describe('fetchWorkoutAiResult', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    apiFetch.mockReset();
  });
  afterEach(() => vi.useRealTimers());

  it('padrao: 20s e rejeita ao esgotar', async () => {
    expect(WORKOUT_AI_POLL_TIMEOUT_MS).toBe(20000);
    jobs(30000);
    const p = fetchWorkoutAiResult('/x').catch((e: Error) => e);
    await vi.advanceTimersByTimeAsync(21000);
    expect(((await p) as Error).message).toMatch(/Tempo esgotado/);
  });

  it('prazo customizado segue consultando apos 20s e resolve quando done', async () => {
    jobs(25000);
    const p = fetchWorkoutAiResult<{ summary: string }>('/x', {}, 90000);
    await vi.advanceTimersByTimeAsync(26000);
    await expect(p).resolves.toEqual({ summary: 'ok' });
  });

  it('prazo customizado esgotado rejeita', async () => {
    jobs(null);
    const p = fetchWorkoutAiResult('/x', {}, 30000).catch((e: Error) => e);
    await vi.advanceTimersByTimeAsync(31000);
    expect(((await p) as Error).message).toMatch(/Tempo esgotado/);
  });
});
