'use client';

import { Center, Loader, Text } from '@mantine/core';
import { useEffect } from 'react';
import { RecoverableError } from '@/components/RecoverableError';
import { ApiError, TRANSIENT_ERROR_MESSAGE } from '@/lib/api';
import { resolveKnownUserId } from '@/lib/offline/scope';
import { useSessionGuard } from '@/lib/use-session-guard';
import { BRAND } from '@/theme';

/**
 * Layout de rotas autenticadas FULL-SCREEN (route group `(fullscreen)`, não
 * aparece na URL) — irmão de `(app)`, reaproveitando a MESMA guarda de sessão
 * (`useSessionGuard`: `GET /auth/me`; 401 redireciona via `apiFetch`, falha
 * transitória mostra "Tentar de novo" sem deslogar — M56), mas
 * deliberadamente SEM `AppShellLayout` (sem sidebar/header do app). Usado por
 * telas imersivas de execução em tempo real onde o usuário está no celular
 * durante a atividade (ex.: `/modo-treino`) — sidebar/menu atrapalhariam o
 * fluxo guiado.
 *
 * Novas telas full-screen autenticadas devem viver dentro deste grupo.
 */
export default function FullscreenAuthenticatedLayout({ children }: { children: React.ReactNode }) {
  const { state, error, retry } = useSessionGuard();

  // Sem rede (falha de conexão, status 0 — nunca 401/403/5xx) e com um usuário
  // já conhecido neste aparelho: abre o treino com os dados locais, sem chamadas
  // autenticadas além da fila. Ao voltar a rede, revalida a sessão de verdade.
  const offlineKnownUser =
    state === 'unavailable' && error instanceof ApiError && error.status === 0 && resolveKnownUserId() != null;
  useEffect(() => {
    if (!offlineKnownUser) return undefined;
    window.addEventListener('online', retry);
    return () => window.removeEventListener('online', retry);
  }, [offlineKnownUser, retry]);

  if (offlineKnownUser) {
    return (
      <>
        <Text size="xs" ta="center" role="status" data-testid="offline-banner" py={4} style={{ background: BRAND.bg }} c={BRAND.textMuted}>
          Offline — dados deste aparelho
        </Text>
        {children}
      </>
    );
  }

  if (state === 'unavailable') {
    return (
      <Center style={{ minHeight: '100vh', background: BRAND.bg }} p="md">
        <RecoverableError
          title="Não foi possível confirmar sua sessão"
          description={`${TRANSIENT_ERROR_MESSAGE} Você continua conectado.`}
          onRetry={retry}
        />
      </Center>
    );
  }

  if (state !== 'ok') {
    return (
      <Center style={{ minHeight: '100vh', background: BRAND.bg }}>
        <Loader color="brand" />
      </Center>
    );
  }

  return <>{children}</>;
}
