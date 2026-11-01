'use client';

import { Button, Group, Paper, Text } from '@mantine/core';
import { usePathname } from 'next/navigation';

import { isSilentPath } from '@/lib/pwa/install-banner-policy';
import { shouldOfferUpdate } from '@/lib/pwa/update-policy';
import type { ServiceWorkerUpdateState } from '@/lib/pwa/use-service-worker';

/** Aviso "Atualização disponível": nunca durante treino ativo (ver `shouldOfferUpdate`). */
export function UpdateNotice({ updateReady, applyUpdate }: ServiceWorkerUpdateState) {
  const pathname = usePathname();
  const show = shouldOfferUpdate({ waitingWorker: updateReady, activeWork: false, silentPath: isSilentPath(pathname) });
  if (!show) return null;

  return (
    <Paper
      role="status"
      aria-live="polite"
      data-testid="pwa-update-notice"
      withBorder
      shadow="md"
      radius="md"
      p="sm"
      style={{
        position: 'fixed',
        left: 12,
        right: 12,
        top: 'calc(12px + env(safe-area-inset-top, 0px))',
        zIndex: 160,
      }}
    >
      <Group justify="space-between" wrap="nowrap" gap="xs">
        <Text size="sm" fw={600}>
          Atualização disponível
        </Text>
        <Button size="compact-md" onClick={() => void applyUpdate()}>
          Atualizar
        </Button>
      </Group>
    </Paper>
  );
}
