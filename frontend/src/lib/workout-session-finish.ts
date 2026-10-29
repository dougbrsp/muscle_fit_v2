// Encerramento de uma sessão de treino (`POST /workout-sessions/{id}/finish`),
// tolerante a repetição.

import { ApiError, apiFetch } from './api';

/**
 * Finaliza a sessão. Uma sessão JÁ finalizada é sucesso, não erro.
 *
 * Bug real (atividade 230, 2026-09-25): a primeira chamada efetivou o
 * encerramento no servidor (`ended_at` gravado) e só a RESPOSTA não voltou —
 * o API Gateway tem teto de 29 s e devolve `{"message":"Endpoint request
 * timed out"}`, que no browser aparece como erro de API/CORS (mesmo
 * precedente do PR #188). Na segunda tentativa o usuário levava 409 "Este
 * treino já foi finalizado." numa faixa vermelha, num treino que estava
 * perfeitamente encerrado, e não tinha como sair da tela.
 *
 * O backend passou a responder 200 para a repetição (`already_finished`), e
 * este tratamento de 409 cobre a janela de deploy (front novo + API antiga) e
 * qualquer outro caminho que ainda finalize por fora (app do relógio,
 * Telegram/MCP) entre as duas tentativas.
 */
export async function finishWorkoutSession(sessionId: number): Promise<void> {
  try {
    await apiFetch(`/workout-sessions/${sessionId}/finish`, { method: 'POST', body: '{}' });
  } catch (error) {
    if (error instanceof ApiError && error.status === 409) return;
    throw error;
  }
}
