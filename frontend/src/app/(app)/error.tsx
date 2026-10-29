'use client';

import { Center } from '@mantine/core';
import { useEffect } from 'react';
import { RecoverableError } from '@/components/RecoverableError';

/**
 * Error boundary das rotas autenticadas (M56). Um erro de render numa tela
 * (ex.: o loop React #185 de `/grupos-musculares`) fica contido no conteúdo:
 * o app shell (`(app)/layout.tsx`, menu e cabeçalho) continua de pé, a sessão
 * não é tocada e "Tentar de novo" re-renderiza só o segmento.
 *
 * Compatível com `output: 'export'`: `error.tsx` é um componente cliente que
 * o Next embute no bundle da rota como React error boundary — não depende de
 * servidor. Ele NÃO pega erro do próprio `(app)/layout.tsx` (fica acima
 * dele); isso é papel do `src/app/global-error.tsx`.
 */
export default function AppError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    // Só o erro técnico, para o console do navegador; nada é enviado.
    console.error(error);
  }, [error]);

  return (
    <Center py="xl">
      <RecoverableError
        title="Algo deu errado nesta tela"
        description="Um erro inesperado impediu esta tela de carregar. Você continua conectado — tente de novo ou use o menu para ir a outra tela."
        onRetry={reset}
      />
    </Center>
  );
}
