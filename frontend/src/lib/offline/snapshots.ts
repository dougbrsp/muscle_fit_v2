// Último retrato de leituras do servidor que o Modo Treino precisa para REABRIR
// sem rede (detalhe da sessão, ficha, equipamentos da academia). Guardado no
// store `meta` da partição do usuário e usado SÓ quando a leitura falha por
// conexão (status 0); com rede, o servidor sempre vence. Sem cálculo nenhum.

import { getOfflineBackend } from './backend';
import { makeKey } from './keys';
import { enqueueBackendTask, onOfflinePurge } from './scope';

const SNAP_PREFIX = 'snap:';
/** Versão do formato do retrato; outra versão é ignorada (nunca interpretada). */
export const SNAPSHOT_VERSION = 1;

type Stored = { v: number; data: unknown };

let snapUser: number | null = null;
let cache = new Map<string, unknown>();

onOfflinePurge(() => {
  snapUser = null;
  cache = new Map();
});

export async function loadSnapshots(userId: number): Promise<void> {
  if (snapUser === userId) return;
  const backend = await getOfflineBackend();
  const rows = await enqueueBackendTask(() => backend.list('meta', makeKey(userId, SNAP_PREFIX)));
  const prefix = makeKey(userId, SNAP_PREFIX);
  const next = new Map<string, unknown>();
  for (const row of rows) {
    const stored = row.value as Stored | null;
    if (stored && stored.v === SNAPSHOT_VERSION) next.set(row.key.slice(prefix.length), stored.data);
  }
  cache = next;
  snapUser = userId;
}

export function readSnapshot<T>(name: string): T | undefined {
  return cache.get(name) as T | undefined;
}

export function writeSnapshot(name: string, value: unknown): void {
  const userId = snapUser;
  if (userId == null) return;
  cache.set(name, value);
  void enqueueBackendTask(async () => {
    if (snapUser !== userId) return;
    try {
      const backend = await getOfflineBackend();
      await backend.put('meta', makeKey(userId, `${SNAP_PREFIX}${name}`), { v: SNAPSHOT_VERSION, data: value } satisfies Stored);
    } catch {
      // cota/armazenamento: o retrato é só conveniência offline
    }
  });
}

function isNetworkFailure(error: unknown): boolean {
  return (error as { status?: unknown } | null)?.status === 0;
}

/**
 * Lê do servidor e guarda o retrato; se a rede falhar e houver retrato do mesmo
 * usuário, devolve-o. Erros de servidor (401/403/404/5xx) sobem como sempre.
 */
export async function fetchWithSnapshot<T>(name: string, fetcher: () => Promise<T>): Promise<T> {
  try {
    const fresh = await fetcher();
    writeSnapshot(name, fresh);
    return fresh;
  } catch (error) {
    if (isNetworkFailure(error)) {
      const stored = readSnapshot<T>(name);
      if (stored !== undefined) return stored;
    }
    throw error;
  }
}
