import type { QueryClient } from '@tanstack/react-query';
import { apiFetch } from '@/lib/api';
import { clearSession, confirmLogoutWithUnsyncedOps } from '@/lib/auth';

/**
 * Ponto UNICO de logout da UI (shell desktop e shell mobile). Com series ainda
 * nao sincronizadas neste aparelho, pede confirmacao explicita antes de sair
 * (cancelar mantem a sessao e a fila); nada e descartado em silencio.
 */
export async function logoutAndRedirect(
  replace: (path: string) => void,
  queryClient: QueryClient,
): Promise<void> {
  if (!(await confirmLogoutWithUnsyncedOps())) return;
  try {
    await apiFetch('/auth/logout', { method: 'POST' });
  } catch {
    // mesmo se a chamada falhar (token ja expirado, rede etc.), a sessao
    // local e limpa e o usuario e redirecionado de qualquer forma.
  } finally {
    clearSession();
    // Sem clear() aqui: com as telas ainda montadas, os observers de
    // ['current-user'] recriariam a query e disparariam GET /auth/me sem cookie;
    // o 401 em api.ts redirecionaria com ?next=<tela antiga>. So cancela o que
    // esta em voo e navega; o cache e limpo na fronteira do login.
    try {
      await queryClient.cancelQueries();
    } catch {
      // falha ao cancelar nao impede a saida.
    } finally {
      replace('/');
    }
  }
}

/**
 * Fronteira do login: descarta todo o cache do Query (current-user, users-admin,
 * grupos...) para que nenhuma conta herde dados da anterior na mesma aba
 * (navegacao client-side nao recarrega). Chamada na montagem do LoginClient,
 * onde nao ha observers de ['current-user'], e no login bem-sucedido.
 */
export function resetSessionCache(queryClient: QueryClient): void {
  queryClient.clear();
}
