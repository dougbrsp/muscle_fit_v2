'use client';

import { Center, Loader } from '@mantine/core';
import { AppShellLayout } from '@/components/AppShellLayout';
import { RecoverableError } from '@/components/RecoverableError';
import { TRANSIENT_ERROR_MESSAGE } from '@/lib/api';
import { useSessionGuard } from '@/lib/use-session-guard';
import { BRAND } from '@/theme';

/**
 * Layout de rotas autenticadas (route group `(app)`, não aparece na URL).
 * Guarda de sessão: como a sessão vive num cookie httpOnly (inacessível a
 * JS), a checagem precisa ser uma chamada real à API (`GET /auth/me`) em vez
 * de uma leitura síncrona de localStorage — ver `useSessionGuard`. Em caso de
 * 401, `apiFetch` já cuida do redirect para `/`. Falha transitória (503,
 * rede) NÃO manda para o login (M56): mostra a mensagem com "Tentar de novo".
 * Novas telas autenticadas devem viver dentro deste grupo para reaproveitar o
 * guard, o app-shell e o error boundary (`error.tsx`).
 *
 * (login vive em `/` — não em `/login` — ver `src/app/page.tsx`.)
 */
export default function AuthenticatedLayout({ children }: { children: React.ReactNode }) {
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

  return <AppShellLayout>{children}</AppShellLayout>;
}
