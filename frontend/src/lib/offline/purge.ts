// Purge e contagem por usuário. Pequeno de propósito: é importado por
// `auth.ts` (clearSession) e precisa iniciar a limpeza de forma síncrona,
// porque o 401 redireciona a página logo em seguida.

import { getOfflineBackend } from './backend';
import { ACTIVE_USER_MARKER_KEY, LEGACY_PARKED_KEY, LEGACY_SESSION_KEY, STORE_NAMES, makeKey, userPrefix } from './keys';
import { OP_KEY_PREFIX, isUnsyncedOp, type OfflineOp } from './op-types';
import {
  clearVerification,
  enqueueBackendTask,
  getActiveUserId,
  notifyOfflinePurge,
  resolveKnownUserId,
  setActiveUserId,
  setOrphanFlag,
} from './scope';

/** Quantas operações do usuário ainda não foram confirmadas pelo servidor. */
export async function countUnsyncedOps(userId: number | null = resolveKnownUserId()): Promise<number> {
  if (userId == null) return 0;
  return enqueueBackendTask(async () => {
    const backend = await getOfflineBackend();
    const rows = await backend.list('op_queue', makeKey(userId, OP_KEY_PREFIX));
    return rows.filter((row) => isUnsyncedOp(row.value as OfflineOp)).length;
  });
}

/**
 * Apaga a partição do usuário. `keepUnsynced` (caminho do 401, sem como pedir
 * confirmação) preserva SÓ as operações ainda não sincronizadas: nada é
 * descartado em silêncio e continuam isoladas por usuário (outra conta não as
 * lê). O logout explícito, depois da confirmação, apaga tudo.
 */
export function purgeOfflineUser(userId: number, options: { keepUnsynced?: boolean } = {}): Promise<void> {
  return enqueueBackendTask(async () => {
    const backend = await getOfflineBackend();
    const prefix = userPrefix(userId);
    let kept = 0;
    for (const store of STORE_NAMES) {
      if (store === 'op_queue' && options.keepUnsynced) {
        const rows = await backend.list('op_queue', prefix);
        for (const row of rows) {
          if (isUnsyncedOp(row.value as OfflineOp)) kept += 1;
          else await backend.delete('op_queue', row.key);
        }
        continue;
      }
      await backend.deletePrefix(store, prefix);
    }
    if (kept === 0 && resolveKnownUserId() === userId) {
      setActiveUserId(null);
      setOrphanFlag(false);
      clearVerification();
    } else if (kept > 0 && resolveKnownUserId() === userId) {
      // O marcador resta só por ops órfãs: não serve para abrir o treino offline.
      setOrphanFlag(true);
    }
  });
}

/**
 * Chamado por `clearSession()`. A parte síncrona (chaves legadas do
 * localStorage, caches em memória) acontece antes de retornar; a parte do
 * backend é enfileirada e a Promise devolvida pode ser aguardada.
 */
export function purgeOfflineData(options: { keepUnsynced?: boolean } = {}): Promise<void> {
  if (typeof window !== 'undefined') {
    try {
      window.localStorage.removeItem(LEGACY_SESSION_KEY);
      window.localStorage.removeItem(LEGACY_PARKED_KEY);
    } catch {
      // storage indisponível
    }
  }
  const userId = getActiveUserId() ?? resolveKnownUserId();
  notifyOfflinePurge();
  if (userId == null) return Promise.resolve();
  if (!options.keepUnsynced && typeof window !== 'undefined') {
    try {
      window.localStorage.removeItem(ACTIVE_USER_MARKER_KEY);
    } catch {
      // ignore
    }
    clearVerification();
    setOrphanFlag(false);
  }
  return purgeOfflineUser(userId, options).catch(() => undefined);
}
