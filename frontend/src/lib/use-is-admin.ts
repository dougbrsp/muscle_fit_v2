'use client';

import { useQuery } from '@tanstack/react-query';
import { apiFetch } from '@/lib/api';
import type { SessionUser } from '@/lib/auth';

/**
 * Hook único de "o usuário logado é admin?". Reaproveita a mesma queryKey
 * `['current-user']` (GET /auth/me) das demais telas, então o cache é
 * compartilhado e não gera requisição extra.
 *
 * Enquanto o usuário carrega (ou se a consulta falhar) devolve `false`: o que é
 * exclusivo de admin fica oculto por padrão e nunca pisca para quem não é.
 */
export async function fetchCurrentUser(): Promise<SessionUser> {
  return apiFetch<SessionUser>('/auth/me');
}

export function useIsAdmin(): boolean {
  const query = useQuery({
    queryKey: ['current-user'],
    queryFn: fetchCurrentUser,
    staleTime: 60_000,
    retry: false,
  });
  return query.data?.role === 'admin';
}
