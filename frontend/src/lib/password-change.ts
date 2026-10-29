import type { QueryClient } from '@tanstack/react-query';
import { ApiError } from './api';

/**
 * Troca de senha (`POST /auth/change-password`) — mensagem de erro e
 * atualização do estado depois do sucesso.
 *
 * A API nunca responde 401 nesta rota (401 = sessão encerrada, e o `apiFetch`
 * redireciona para o login): senha atual errada é 400
 * `{detail, code: 'wrong_current_password'}` e conta bloqueada por tentativas
 * é 429 `{detail, code: 'account_locked'}`. O `detail` já vem pronto para a
 * tela; aqui só há o texto de reserva para erro sem `ApiError`.
 */

export const PASSWORD_CHANGE_FALLBACK_ERROR = 'Não foi possível trocar a senha.';

export function passwordChangeErrorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : PASSWORD_CHANGE_FALLBACK_ERROR;
}

/** queryKey de `GET /auth/me` usada pelo app-shell e pelas telas admin. */
export const CURRENT_USER_QUERY_KEY = ['current-user'] as const;

/**
 * Depois da troca bem-sucedida o backend zerou `must_change_password`:
 * refaz `GET /auth/me` (o app-shell passa a ver `must_change_password: false`)
 * e as consultas que falharam enquanto a troca estava pendente (403
 * `must_change_password`), para a tela sair do estado de erro sem reload.
 */
export async function refreshAfterPasswordChange(queryClient: QueryClient): Promise<void> {
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: CURRENT_USER_QUERY_KEY }),
    queryClient.invalidateQueries({ predicate: (query) => query.state.status === 'error' }),
  ]);
}
