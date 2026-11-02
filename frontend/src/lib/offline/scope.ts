// Escopo por usuário do armazenamento offline: quem é o usuário ativo neste
// aparelho, fila serial de tarefas no backend (escritas e purge nunca se
// atropelam) e ganchos de purge. Módulo pequeno e sem dependências pesadas:
// é importado por `auth.ts` (logout/401) e por isso entra no bundle comum.

import { ACTIVE_USER_MARKER_KEY, LAST_VERIFIED_KEY, ORPHAN_FLAG_KEY } from './keys';

/**
 * Política de retenção/segurança (não é regra de treino): o "último usuário
 * conhecido" só vale para abrir offline até 7 dias depois da última confirmação
 * por `/auth/me` 200.
 */
export const KNOWN_USER_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000;

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

function safeStorage(): Storage | null {
  try {
    return typeof window === 'undefined' ? null : window.localStorage;
  } catch {
    return null;
  }
}

/** Registra que `/auth/me` confirmou o usuário agora (e que o marcador não é mais só de ops órfãs). */
export function markUserVerified(now = Date.now()): void {
  const storage = safeStorage();
  try {
    storage?.setItem(LAST_VERIFIED_KEY, String(now));
    storage?.removeItem(ORPHAN_FLAG_KEY);
  } catch {
    // sem storage: o fallback offline simplesmente não valerá
  }
}

export function setOrphanFlag(on: boolean): void {
  try {
    if (on) safeStorage()?.setItem(ORPHAN_FLAG_KEY, '1');
    else safeStorage()?.removeItem(ORPHAN_FLAG_KEY);
  } catch {
    // ignore
  }
}

export function clearVerification(): void {
  try {
    safeStorage()?.removeItem(LAST_VERIFIED_KEY);
  } catch {
    // ignore
  }
}

/**
 * Usuário que PODE abrir o treino sem rede: marcador presente, confirmado por
 * `/auth/me` há menos de 7 dias e que não resta só por ops órfãs (sessão já
 * encerrada por 401). Fora disso, `null`: o app exige a rede/login.
 */
export function knownUserForOffline(now = Date.now()): number | null {
  const id = resolveKnownUserId();
  const storage = safeStorage();
  if (id == null || !storage) return null;
  try {
    if (storage.getItem(ORPHAN_FLAG_KEY) === '1') return null;
    const verified = Number(storage.getItem(LAST_VERIFIED_KEY));
    if (!Number.isFinite(verified) || verified <= 0 || now - verified > KNOWN_USER_MAX_AGE_MS) return null;
  } catch {
    return null;
  }
  return id;
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
