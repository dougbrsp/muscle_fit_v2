'use client';

import { Alert, Button, Stack, Text } from '@mantine/core';
import { IconAlertTriangle, IconRefresh } from '@tabler/icons-react';
import type { ReactNode } from 'react';

/**
 * Falha recuperável com "Tentar de novo" (M56). Usado pelo error boundary das
 * rotas autenticadas (`error.tsx`) e pela guarda de sessão quando `/auth/me`
 * não pôde ser confirmado (503/rede) — nos dois casos o usuário continua
 * logado e só precisa repetir.
 */
export function RecoverableError({
  title,
  description,
  onRetry,
}: {
  title: string;
  description: ReactNode;
  onRetry: () => void;
}) {
  return (
    <Alert
      color="red"
      variant="light"
      icon={<IconAlertTriangle size={18} />}
      title={title}
      role="alert"
      maw={560}
      w="100%"
    >
      <Stack gap="sm" align="flex-start">
        <Text size="sm">{description}</Text>
        <Button color="brand" leftSection={<IconRefresh size={16} />} onClick={onRetry}>
          Tentar de novo
        </Button>
      </Stack>
    </Alert>
  );
}
