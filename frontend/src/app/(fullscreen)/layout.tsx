'use client';

import { Center, Loader } from '@mantine/core';
import { RecoverableError } from '@/components/RecoverableError';
import { TRANSIENT_ERROR_MESSAGE } from '@/lib/api';
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
  const { state, retry } = useSessionGuard();

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
