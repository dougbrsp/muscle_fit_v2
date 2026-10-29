import { ApiError } from './api';

/**
 * Como a guarda de sessão dos layouts autenticados (`GET /auth/me`) reage a
 * uma falha (M56):
 *
 * - `unauthenticated` (só 401): a sessão acabou de verdade. `apiFetch` já
 *   limpou o estado local e está redirecionando para o login — a guarda não
 *   faz mais nada.
 * - `unavailable` (503, rede, 5xx, qualquer outra coisa): não foi possível
 *   CONFIRMAR a sessão, o que não é o mesmo que ela não existir. A guarda
 *   mostra a mensagem com "Tentar de novo" e NUNCA manda para o login.
 *
 * Antes do M56 qualquer falha aqui fazia `router.replace('/')`: um 503 do
 * Aurora voltando do auto-pause virava "deslogado".
 */
export type SessionCheckFailure = 'unauthenticated' | 'unavailable';

export function classifySessionCheckError(error: unknown): SessionCheckFailure {
  return error instanceof ApiError && error.status === 401 ? 'unauthenticated' : 'unavailable';
}
