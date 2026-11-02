'use client';

import { Alert, Text } from '@mantine/core';
import { IconAlertTriangle } from '@tabler/icons-react';
import { useSyncExternalStore } from 'react';
import { getStorageStatus, subscribeStorageStatus } from '@/lib/offline/session-store';

/**
 * Aviso discreto quando o armazenamento durável não é confiável: IndexedDB
 * indisponível (modo privado) ou falha de gravação no aparelho. Texto puro,
 * sem regra de negócio; some quando o armazenamento volta ao normal.
 */
export function StorageStatusNotice() {
  const status = useSyncExternalStore(subscribeStorageStatus, getStorageStatus, getStorageStatus);
  const message = status.writeError
    ? 'Não foi possível gravar neste aparelho agora. O que você registrar pode não ser mantido se a página fechar.'
    : status.degraded
      ? 'O armazenamento seguro do navegador não está disponível (talvez a aba seja privada). Seus dados locais podem ser perdidos ao fechar.'
      : null;
  if (!message) return null;
  return (
    <Alert color="yellow" variant="light" icon={<IconAlertTriangle size={16} />} role="status" py={6} px="sm">
      <Text size="xs">{message}</Text>
    </Alert>
  );
}
