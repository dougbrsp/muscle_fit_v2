import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from './api';
import { finishWorkoutSession } from './workout-session-finish';

vi.mock('./api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./api')>();
  return { ...actual, apiFetch: vi.fn() };
});

const { apiFetch } = await import('./api');
const apiFetchMock = vi.mocked(apiFetch);

describe('finishWorkoutSession (segunda tentativa de encerrar virava erro de usuário)', () => {
  beforeEach(() => {
    apiFetchMock.mockReset();
  });

  it('chama o endpoint de finish da sessão', async () => {
    apiFetchMock.mockResolvedValue({ status: 'ok' });
    await finishWorkoutSession(50);
    expect(apiFetchMock).toHaveBeenCalledWith('/workout-sessions/50/finish', { method: 'POST', body: '{}' });
  });

  it('409 "Este treino já foi finalizado." é sucesso: o objetivo do usuário está cumprido', async () => {
    // Cenário sintético baseado em caso real (2026-09-25): a 1ª chamada gravou
    // `ended_at` e estourou o teto de 29 s do API Gateway; a 2ª recebia 409 e
    // a tela mostrava faixa vermelha num treino já encerrado.
    apiFetchMock.mockRejectedValue(new ApiError('Este treino já foi finalizado.', 409));
    await expect(finishWorkoutSession(50)).resolves.toBeUndefined();
  });

  it('erro de rede/timeout continua sendo erro (o usuário precisa poder tentar de novo)', async () => {
    apiFetchMock.mockRejectedValue(new ApiError('Endpoint request timed out', 504));
    await expect(finishWorkoutSession(50)).rejects.toBeInstanceOf(ApiError);
  });

  it('404 (sessão de outro usuário/inexistente) continua sendo erro', async () => {
    apiFetchMock.mockRejectedValue(new ApiError('Sessão de treino não encontrada.', 404));
    await expect(finishWorkoutSession(50)).rejects.toBeInstanceOf(ApiError);
  });
});
