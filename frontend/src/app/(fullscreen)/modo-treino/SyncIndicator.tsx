'use client';

import { Alert, Button, Group, Text } from '@mantine/core';
import { IconCloudOff, IconCloudUpload } from '@tabler/icons-react';
import { useSyncExternalStore } from 'react';
import { opQueue } from '@/lib/offline/queue';
import type { OfflineOp } from '@/lib/offline/op-types';

function plural(count: number, one: string, many: string): string {
  return count === 1 ? `1 ${one}` : `${count} ${many}`;
}

/**
 * Indicador de pendências de sincronização (só apresentação). Mostra o que
 * ainda NÃO está confirmado pelo servidor: nunca declara "sincronizado" sem a
 * resposta 2xx (o estado vem de `opQueue`). Em conflito, oferece a ação ao
 * usuário (tentar de novo / manter o do servidor) em vez de decidir sozinho.
 */
export function SyncIndicator() {
  const snapshot = useSyncExternalStore(opQueue.subscribe, opQueue.getSnapshot, opQueue.getSnapshot);
  if (snapshot.unsynced === 0) return null;

  const conflicts = snapshot.ops.filter((op: OfflineOp) => op.status === 'conflict');
  const waiting = snapshot.pending;

  return (
    <div role="status" aria-live="polite" data-testid="sync-indicator">
      {waiting > 0 && (
        <Alert color={snapshot.error > 0 ? 'orange' : 'blue'} variant="light" icon={<IconCloudUpload size={16} />} py={6} px="sm">
          <Group justify="space-between" gap="xs" wrap="nowrap">
            <Text size="xs">
              {plural(waiting, 'registro salvo no aparelho, aguardando envio', 'registros salvos no aparelho, aguardando envio')}
              {snapshot.error > 0 ? ' (o envio falhou; vamos tentar de novo)' : ''}
            </Text>
            <Button size="compact-xs" variant="subtle" onClick={() => void opQueue.flush()}>
              Enviar agora
            </Button>
          </Group>
        </Alert>
      )}
      {conflicts.map((op: OfflineOp) => (
        <Alert key={op.opId} color="red" variant="light" icon={<IconCloudOff size={16} />} py={6} px="sm" mt={4}>
          <Text size="xs" mb={4}>
            Não foi possível enviar um registro: {op.lastError ?? 'o servidor recusou.'}
          </Text>
          <Group gap="xs">
            <Button size="compact-xs" variant="default" onClick={() => void opQueue.resolveConflict(op.opId, 'resend')}>
              Tentar de novo
            </Button>
            <Button size="compact-xs" variant="default" onClick={() => void opQueue.resolveConflict(op.opId, 'keep_server')}>
              Manter o do servidor
            </Button>
          </Group>
        </Alert>
      ))}
    </div>
  );
}
