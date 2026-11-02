import { apiFetch } from '@/lib/api';
import { clearSession, confirmLogoutWithUnsyncedOps } from '@/lib/auth';

/**
 * Ponto UNICO de logout da UI (shell desktop e shell mobile). Com series ainda
 * nao sincronizadas neste aparelho, pede confirmacao explicita antes de sair
 * (cancelar mantem a sessao e a fila); nada e descartado em silencio.
 */
export async function logoutAndRedirect(replace: (path: string) => void): Promise<void> {
  if (!(await confirmLogoutWithUnsyncedOps())) return;
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
