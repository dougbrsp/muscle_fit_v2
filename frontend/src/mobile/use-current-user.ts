'use client';

import { useQuery } from '@tanstack/react-query';
import { apiFetch } from '@/lib/api';
import type { SessionUser } from '@/lib/auth';

/**
 * Mesma chave e mesma chamada do `AppShellLayout` (`['current-user']`): o
 * cache do TanStack Query é compartilhado, então trocar de viewport não
 * refaz `GET /auth/me`.
 */
export function useCurrentUser() {
  return useQuery({
    queryKey: ['current-user'],
    queryFn: () => apiFetch<SessionUser>('/auth/me'),
    staleTime: 60_000,
    retry: false,
  });
}
