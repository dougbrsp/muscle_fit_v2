'use client';

import { useQuery } from '@tanstack/react-query';
import type { SessionUser } from '@/lib/auth';
import { can, type Permissions, type Scope } from '@/lib/permissions';
import { fetchCurrentUser } from '@/lib/use-is-admin';

export type PermissionsApi = {
  /** Há dados de `/auth/me` (inclusive em cache após um refetch que falhou). */
  ready: boolean;
  can: (resource: string, action?: string, minScope?: Scope) => boolean;
};

/**
 * Derivação pura dos dados de `/auth/me`: decide pela presença de `user`, não
 * pelo status da query (refetch com erro mantém `data`). Sem dados ou com
 * `permissions` nulo/ausente, `can` devolve sempre `false` (esconde).
 */
export function permissionsApi(user: Pick<SessionUser, 'permissions'> | undefined): PermissionsApi {
  const perms: Permissions | null = user?.permissions ?? null;
  return {
    ready: user !== undefined,
    can: (resource, action = 'ver', minScope) => can(perms, resource, action, minScope),
  };
}

/**
 * Permissões do usuário logado. Reaproveita a query `['current-user']`
 * (GET /auth/me) de `useIsAdmin`: cache compartilhado, sem requisição extra.
 */
export function usePermissions(): PermissionsApi {
  const query = useQuery({
    queryKey: ['current-user'],
    queryFn: fetchCurrentUser,
    staleTime: 60_000,
    retry: false,
  });
  return permissionsApi(query.data);
}
