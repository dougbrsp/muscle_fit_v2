// Guarda de rota das telas de catálogo só de admin ("Equipamentos x
// Exercícios" e "Domínio de Exercícios", decisão do usuário em 2026-10-13:
// role EXATAMENTE "admin"; personal trainer também não entra). O backend
// continua sendo a garantia real da escrita; a guarda só tira a tela de quem
// não é admin e manda para "Preferências de Treino", onde a nota é dada.

export const ADMIN_ROUTE_FALLBACK = '/preferencias-treino';

export type AdminRouteState = 'loading' | 'allow' | 'redirect';

export function isAdminRole(role: string | null | undefined): boolean {
  return role === 'admin';
}

// `/auth/me` com erro também redireciona (sem loader eterno); a sessão em si
// continua sendo tratada pelo app-shell.
export function adminRouteState(input: {
  isSuccess: boolean;
  isError?: boolean;
  role: string | null | undefined;
}): AdminRouteState {
  if (input.isError) return 'redirect';
  if (!input.isSuccess) return 'loading';
  return isAdminRole(input.role) ? 'allow' : 'redirect';
}
