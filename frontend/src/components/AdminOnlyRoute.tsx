'use client';

import { Group, Loader } from '@mantine/core';
import { useQuery } from '@tanstack/react-query';
import { useRouter } from 'next/navigation';
import { useEffect, type ReactNode } from 'react';
import { apiFetch } from '@/lib/api';
import { ADMIN_ROUTE_FALLBACK, adminRouteState } from '@/lib/admin-route';
import type { SessionUser } from '@/lib/auth';
import { CURRENT_USER_QUERY_KEY } from '@/lib/password-change';

/**
 * Só renderiza `children` para admin. Enquanto `/auth/me` carrega, mostra um
 * loader (nada da tela vaza); quem não é admin é levado para
 * `/preferencias-treino`. Mesma query key do `AppShellLayout`: o React Query
 * dedupa, sem chamada extra.
 */
export function AdminOnlyRoute({ children }: { children: ReactNode }) {
  const router = useRouter();
  const currentUserQuery = useQuery({
    queryKey: CURRENT_USER_QUERY_KEY,
    queryFn: () => apiFetch<SessionUser>('/auth/me'),
    staleTime: 60_000,
    retry: false,
  });
  const state = adminRouteState({
    isSuccess: currentUserQuery.isSuccess,
    isError: currentUserQuery.isError,
    role: currentUserQuery.data?.role,
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
