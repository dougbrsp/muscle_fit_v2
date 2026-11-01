import { apiFetch } from '@/lib/api';
import { clearSession } from '@/lib/auth';

/**
 * Ponto UNICO de logout da UI (shell desktop e shell mobile). A frente B
 * acrescenta aqui, antes do POST, `if (!(await confirmLogoutWithUnsyncedOps())) return;`.
 */
export async function logoutAndRedirect(replace: (path: string) => void): Promise<void> {
  try {
    await apiFetch('/auth/logout', { method: 'POST' });
  } catch {
    // mesmo se a chamada falhar (token ja expirado, rede etc.), a sessao
    // local e limpa e o usuario e redirecionado de qualquer forma.
  } finally {
    clearSession();
    replace('/');
  }
}
