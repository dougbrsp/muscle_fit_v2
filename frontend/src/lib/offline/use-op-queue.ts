'use client';

import { useSyncExternalStore } from 'react';
import { opQueue } from './queue';
import type { OpStatus } from './op-types';
import type { QueueSnapshot } from './op-queue';

/** Estado atual da fila (pendências, erros, conflitos e as ops). */
export function useOpQueue(): QueueSnapshot {
  return useSyncExternalStore(opQueue.subscribe, opQueue.getSnapshot, opQueue.getSnapshot);
}

/** Estado de sincronização de uma série pela `client_key` (null = não está na fila). */
export function useOpStatus(clientKey: string | null): OpStatus | null {
  const snapshot = useOpQueue();
  if (!clientKey) return null;
  return snapshot.ops.find((op) => op.clientKey === clientKey)?.status ?? null;
}
