import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// Prazos de polling do Modo Treino: turnos curtos (por serie/exercicio) seguem
// em 20s; o resumo do treino inteiro espera ate o teto de 90s do helper
// generico (a Lambda tem timeout de 60s; um job real levou 22s).
const apiFetch = vi.fn();
vi.mock('./api', async (importOriginal) => ({ ...(await importOriginal<typeof import('./api')>()), apiFetch }));

const { fetchWorkoutAiResult, fetchWorkoutSummary } = await import('./workout-mode-ai');

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

// Observa se a promessa ja liquidou sem aguardar.
function observar<T>(p: Promise<T>) {
  const estado: { fim: boolean; valor?: unknown } = { fim: false };
  p.then(
    (v) => Object.assign(estado, { fim: true, valor: v }),
    (e) => Object.assign(estado, { fim: true, valor: e }),
  );
  return estado;
}

describe('prazos de polling do Modo Treino', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    apiFetch.mockReset();
  });
  afterEach(() => vi.useRealTimers());

  it('fetchWorkoutAiResult: padrao segue em 20s', async () => {
    jobs(null);
    const estado = observar(fetchWorkoutAiResult('/x'));
    await vi.advanceTimersByTimeAsync(19000);
    expect(estado.fim).toBe(false);
    await vi.advanceTimersByTimeAsync(2000);
    expect(estado.fim).toBe(true);
    expect((estado.valor as Error).message).toMatch(/Tempo esgotado/);
  });

  it('fetchWorkoutSummary: consulta depois dos 20s e resolve quando done', async () => {
    jobs(25000);
    const p = fetchWorkoutSummary(261);
    await vi.advanceTimersByTimeAsync(26000);
    await expect(p).resolves.toEqual({ summary: 'ok' });
    expect(apiFetch.mock.calls[0][0]).toBe('/activities/261/ai-summary/generate');
  });

  it('fetchWorkoutSummary: continua pendente aos 29s e rejeita so no teto de 90s', async () => {
    jobs(null);
    const estado = observar(fetchWorkoutSummary(261));
    await vi.advanceTimersByTimeAsync(29000);
    expect(estado.fim).toBe(false);
    await vi.advanceTimersByTimeAsync(59000);
    expect(estado.fim).toBe(false);
    await vi.advanceTimersByTimeAsync(4000);
    expect(estado.fim).toBe(true);
    expect((estado.valor as Error).message).toMatch(/Tempo esgotado/);
  });
});
