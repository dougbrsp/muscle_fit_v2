'use client';

import { MantineProvider } from '@mantine/core';
import { Notifications } from '@mantine/notifications';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useState } from 'react';
import { theme } from '@/theme';

export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            // Sem `defaultOptions`, o React Query usa `staleTime: 0`: toda
            // navegação (e todo remount) refazia TODAS as consultas
            // analíticas do hub, que são caras (`get_muscle_group_overview`
            // lê o histórico inteiro). 60 s é conservador: alternar entre as
            // abas do dashboard reaproveita o cache, e um treino registrado
            // agora aparece no próximo minuto.
            staleTime: 60_000,
            // Voltar para a aba do navegador não é sinal de que o dado mudou.
            // Telas que PRECISAM de atualização agressiva continuam intactas:
            // `refetchInterval` é independente de `staleTime` e sobrepõe este
            // default (`/recuperacao-muscular` com 60 s em background, e os
            // polls de job de 2 s em `/planos-treino`, `/relatorios` e
            // `/agente-cardio`).
            refetchOnWindowFocus: false,
          },
        },
      }),
  );

  return (
    <QueryClientProvider client={queryClient}>
      <MantineProvider theme={theme} defaultColorScheme="dark">
        <Notifications position="top-right" />
        {children}
      </MantineProvider>
    </QueryClientProvider>
  );
}
