'use client';

import { Group, Loader } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useRouter } from 'next/navigation';
import { useEffect, type ReactNode } from 'react';
import { apiFetch } from '@/lib/api';
import { ADMIN_ROUTE_FALLBACK, permissionRouteState } from '@/lib/admin-route';
import type { SessionUser } from '@/lib/auth';
import { CURRENT_USER_QUERY_KEY } from '@/lib/password-change';
import type { Scope } from '@/lib/permissions';

/**
 * Só renderiza `children` para quem tem `resource`/`action` (padrão `ver`) com
 * escopo ao menos `minScope`. Enquanto `/auth/me` carrega, mostra um loader
 * (nada da tela vaza); sem a permissão (ou em erro) leva para
 * `/preferencias-treino`. Mesma query key do `AppShellLayout`: o React Query
 * dedupa, sem chamada extra. O backend segue como a barreira real.
 */
export function PermissionRoute({
  resource,
  action,
  minScope,
  children,
}: {
  resource: string;
  action?: string;
  minScope?: Scope;
  children: ReactNode;
}) {
  const router = useRouter();
  const currentUserQuery = useQuery({
    queryKey: CURRENT_USER_QUERY_KEY,
    queryFn: () => apiFetch<SessionUser>('/auth/me'),
    staleTime: 60_000,
    retry: false,
  });
  const state = permissionRouteState({
    hasData: currentUserQuery.data !== undefined,
    isError: currentUserQuery.isError,
    permissions: currentUserQuery.data?.permissions,
    required: { resource, action, minScope },
  });

  useEffect(() => {
    if (state === 'redirect') router.replace(ADMIN_ROUTE_FALLBACK);
  }, [state, router]);

  if (state !== 'allow') {
    return (
      <Group justify="center" py="xl">
        <Loader color="brand" />
      </Group>
    );
  }
  return <>{children}</>;
}
