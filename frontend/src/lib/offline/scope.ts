// Escopo por usuário do armazenamento offline: quem é o usuário ativo neste
// aparelho, fila serial de tarefas no backend (escritas e purge nunca se
// atropelam) e ganchos de purge. Módulo pequeno e sem dependências pesadas:
// é importado por `auth.ts` (logout/401) e por isso entra no bundle comum.

import { ACTIVE_USER_MARKER_KEY } from './keys';

let activeUserId: number | null = null;

export function getActiveUserId(): number | null {
  return activeUserId;
}

export function setActiveUserId(userId: number | null): void {
  activeUserId = userId;
  if (typeof window === 'undefined') return;
  try {
    if (userId == null) window.localStorage.removeItem(ACTIVE_USER_MARKER_KEY);
    else window.localStorage.setItem(ACTIVE_USER_MARKER_KEY, String(userId));
  } catch {
    // localStorage indisponível: o escopo continua válido em memória.
  }
}

/** Usuário ativo em memória ou, na falta, o último marcado neste aparelho (só o id). */
export function resolveKnownUserId(): number | null {
  if (activeUserId != null) return activeUserId;
  if (typeof window === 'undefined') return null;
  try {
    const raw = window.localStorage.getItem(ACTIVE_USER_MARKER_KEY);
    const parsed = raw == null ? NaN : Number(raw);
    return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
  } catch {
    return null;
  }
}

// Fila serial: toda leitura/escrita/purge no backend passa por aqui, então um
// purge enfileirado depois de uma escrita sempre a enxerga e a apaga.
let chain: Promise<unknown> = Promise.resolve();

export function enqueueBackendTask<T>(task: () => Promise<T>): Promise<T> {
  const run = chain.then(task, task);
  chain = run.catch(() => undefined);
  return run;
}

/** Espera todas as tarefas já enfileiradas (testes e antes de decisões de sincronismo). */
export function flushBackendTasks(): Promise<void> {
  return chain.then(() => undefined);
}

// Ganchos chamados de forma SÍNCRONA no purge (zerar caches em memória).
const purgeListeners = new Set<() => void>();

export function onOfflinePurge(listener: () => void): () => void {
  purgeListeners.add(listener);
  return () => purgeListeners.delete(listener);
}

export function notifyOfflinePurge(): void {
  for (const listener of purgeListeners) {
    try {
      listener();
    } catch {
      // um ouvinte com falha não impede o purge
    }
  }
}
