// Provedor de `hasActiveWork()` (contrato da Frente C em `lib/mobile/active-work.ts`):
// há sessão de treino local em andamento OU operações offline não confirmadas?
// Lê direto do armazenamento (não depende de a tela do treino estar montada),
// então funciona em qualquer página do app. Erro = o contrato trata como `true`.

import { getOfflineBackend } from './backend';
import { makeKey } from './keys';
import { countUnsyncedOps } from './purge';
import { enqueueBackendTask, resolveKnownUserId } from './scope';
import { isOfflineStoreReady, readSession } from './session-store';

export async function offlineHasActiveWork(): Promise<boolean> {
  const userId = resolveKnownUserId();
  if (userId == null) return false;
  if (isOfflineStoreReady() && readSession()) return true;
  const hasSession = await enqueueBackendTask(async () => {
    const backend = await getOfflineBackend();
    return (await backend.get('workout_session', makeKey(userId, 'session'))) != null;
  });
  if (hasSession) return true;
  return (await countUnsyncedOps(userId)) > 0;
}
