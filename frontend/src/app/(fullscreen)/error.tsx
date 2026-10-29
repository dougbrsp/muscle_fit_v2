'use client';

import { Center } from '@mantine/core';
import { useEffect } from 'react';
import { RecoverableError } from '@/components/RecoverableError';
import { BRAND } from '@/theme';

/**
 * Error boundary das telas full-screen (ex.: `/modo-treino`), mesmo papel do
 * `(app)/error.tsx` (M56): erro de render no meio do treino não derruba o app
 * nem a sessão; "Tentar de novo" re-renderiza a tela.
 */
export default function FullscreenError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <Center style={{ minHeight: '100vh', background: BRAND.bg }} p="md">
      <RecoverableError
        title="Algo deu errado nesta tela"
        description="Um erro inesperado impediu esta tela de carregar. Você continua conectado — tente de novo."
        onRetry={reset}
      />
    </Center>
  );
}
