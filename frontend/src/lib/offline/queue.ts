// Instância única da fila offline da página (nunca duas: rotação/remontagem
// reutilizam esta) e a ativação conjunta sessão + fila para o usuário atual.

import { createOpQueue } from './op-queue';
import { apiOpTransport } from './op-transport';
import { loadSnapshots } from './snapshots';
import { activateOfflineUser, type ActivateOptions, type StorageStatus } from './session-store';

export const opQueue = createOpQueue({ transport: apiOpTransport });

export async function activateOffline(userId: number, options: ActivateOptions = {}): Promise<StorageStatus> {
  const status = await activateOfflineUser(userId, options);
  await opQueue.hydrate(userId, { force: options.force });
  await loadSnapshots(userId);
  return status;
}
