// Armazenamento durável da sessão de treino por usuário (IndexedDB, com
// fallback). A API pública de `workout-mode-storage.ts` é SÍNCRONA (as telas
// leem no render), então este módulo mantém um espelho em memória hidratado
// uma vez por usuário (`activateOfflineUser`) e grava em segundo plano
// (write-behind, serializado). Nunca há dois escritores: o espelho é único
// por página, e rotação/resize não reidratam nem reiniciam nada.

import type { StoredWorkoutModeSession } from '../workout-mode-types';
import { getOfflineBackend, type BackendKind } from './backend';
import { LEGACY_PARKED_KEY, LEGACY_SESSION_KEY, makeKey } from './keys';
import { purgeOfflineUser } from './purge';
import { enqueueBackendTask, markUserVerified, onOfflinePurge, resolveKnownUserId, setActiveUserId } from './scope';

const SESSION_NAME = 'session';
const PARKED_NAME = 'parked';

export type LegacyOwnership = 'owned' | 'foreign' | 'unknown';

export type StorageStatus = {
  /** null até a ativação. */
  kind: BackendKind | null;
  /** true quando NÃO é IndexedDB: os dados podem não sobreviver (modo privado etc.). */
  degraded: boolean;
  /** Resultado de `navigator.storage.persist()`; null = indisponível/não pedido. */
  persisted: boolean | null;
  /** Última falha de gravação (cota, etc.); null = nenhuma. */
  writeError: string | null;
};

type State = {
  userId: number | null;
  hydrated: boolean;
  /** true depois de um purge: gravações são descartadas (nunca caem em chave legada). */
  purged: boolean;
  session: StoredWorkoutModeSession | null;
  parked: StoredWorkoutModeSession | null;
};

const state: State = { userId: null, hydrated: false, purged: false, session: null, parked: null };
let status: StorageStatus = { kind: null, degraded: false, persisted: null, writeError: null };
const statusListeners = new Set<() => void>();
let activating: { userId: number; promise: Promise<StorageStatus> } | null = null;

function setStatus(patch: Partial<StorageStatus>): void {
  status = { ...status, ...patch };
  for (const listener of statusListeners) listener();
}

export function getStorageStatus(): StorageStatus {
  return status;
}

export function subscribeStorageStatus(listener: () => void): () => void {
  statusListeners.add(listener);
  return () => statusListeners.delete(listener);
}

onOfflinePurge(() => {
  state.userId = null;
  state.hydrated = false;
  state.purged = true;
  state.session = null;
  state.parked = null;
  activating = null;
});

function isStoredSession(value: unknown): value is StoredWorkoutModeSession {
  return Boolean(value) && typeof (value as { session_id?: unknown }).session_id === 'number';
}

function readLegacy(key: string): StoredWorkoutModeSession | null {
  if (typeof window === 'undefined') return null;
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    return isStoredSession(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

function removeLegacy(key: string): void {
  try {
    window.localStorage.removeItem(key);
  } catch {
    // ignore
  }
}

export type ActivateOptions = {
  /** O usuário acaba de ser confirmado por `/auth/me` 200 (renova a validade do modo offline). */
  verified?: boolean;
  /** Recarrega do disco mesmo já hidratado (ex.: depois de outra aba ter escrito). */
  force?: boolean;
  /**
   * Confere se a sessão guardada na chave legada (sem escopo) pertence ao
   * usuário atual (ex.: `GET /activities/{id}`). Sem isto, a chave legada é
   * descartada em vez de importada (nunca se atribui dado a uma conta sem prova).
   */
  validateLegacySession?: (sessionId: number) => Promise<LegacyOwnership>;
};

async function migrateLegacySlot(
  userId: number,
  legacyKey: string,
  name: string,
  current: StoredWorkoutModeSession | null,
  validate: ActivateOptions['validateLegacySession'],
): Promise<StoredWorkoutModeSession | null> {
  const legacy = readLegacy(legacyKey);
  if (!legacy) return current;
  // Slot do usuário já ocupado: o dado legado é mais antigo; descarta.
  if (current) {
    removeLegacy(legacyKey);
    return current;
  }
  let ownership: LegacyOwnership = 'unknown';
  if (validate) {
    try {
      ownership = await validate(legacy.session_id);
    } catch {
      ownership = 'unknown';
    }
  }
  // Sem como provar a posse: não atribui a ninguém, mas também não apaga (tenta depois).

  if (ownership === 'unknown') return current; // sem rede/erro: tenta de novo na próxima ativação
  removeLegacy(legacyKey);
  if (ownership === 'foreign') return current;
  const backend = await getOfflineBackend();
  await backend.put('workout_session', makeKey(userId, name), legacy);
  return legacy;
}

async function requestPersistence(): Promise<void> {
  try {
    if (typeof navigator !== 'undefined' && navigator.storage?.persist) {
      setStatus({ persisted: await navigator.storage.persist() });
    }
  } catch {
    setStatus({ persisted: null });
  }
}

/**
 * Hidrata o espelho do usuário (idempotente: chamar de novo para o MESMO
 * usuário não reidrata nem apaga estado em memória). Troca de usuário zera o
 * espelho antes de carregar a partição do novo.
 */
export function activateOfflineUser(userId: number, options: ActivateOptions = {}): Promise<StorageStatus> {
  if (state.hydrated && state.userId === userId && !options.force) {
    if (options.verified) markUserVerified();
    return Promise.resolve(status);
  }
  if (activating && activating.userId === userId) return activating.promise;
  // Login CONFIRMADO de outra conta neste aparelho: a anterior não volta a estas
  // ops sem novo login (houve 401/logout), então tudo dela é apagado, inclusive
  // séries não sincronizadas (política documentada em ADR/relatório B5).
  const previousUserId = resolveKnownUserId();
  if (options.verified && previousUserId != null && previousUserId !== userId) {
    void purgeOfflineUser(previousUserId).catch(() => undefined);
  }
  const promise = (async () => {
    if (state.userId !== userId) {
      state.session = null;
      state.parked = null;
    }
    state.userId = userId;
    state.hydrated = false;
    state.purged = false;
    const backend = await getOfflineBackend();
    const [storedSession, storedParked] = await enqueueBackendTask(async () => [
      (await backend.get('workout_session', makeKey(userId, SESSION_NAME))) as unknown,
      (await backend.get('workout_session', makeKey(userId, PARKED_NAME))) as unknown,
    ]);
    let session = isStoredSession(storedSession) ? storedSession : null;
    let parked = isStoredSession(storedParked) ? storedParked : null;
    session = await migrateLegacySlot(userId, LEGACY_SESSION_KEY, SESSION_NAME, session, options.validateLegacySession);
    parked = await migrateLegacySlot(userId, LEGACY_PARKED_KEY, PARKED_NAME, parked, options.validateLegacySession);
    // Se um purge/troca de usuário aconteceu durante a hidratação, descarta o resultado.
    if (state.userId !== userId || state.purged) return status;
    state.session = session;
    state.parked = parked;
    state.hydrated = true;
    setActiveUserId(userId);
    if (options.verified) markUserVerified();
    setStatus({ kind: backend.kind, degraded: backend.kind !== 'indexeddb' });
    void requestPersistence();
    return status;
  })();
  activating = { userId, promise };
  promise.then(
    () => {
      if (activating?.promise === promise) activating = null;
    },
    () => {
      if (activating?.promise === promise) activating = null;
      state.userId = null;
    },
  );
  return promise;
}

/** True quando há um usuário ativo e hidratado (a fachada usa o espelho; senão cai no legado). */
export function isOfflineStoreReady(): boolean {
  return state.hydrated && state.userId != null;
}

/** True após purge: gravações devem ser descartadas, nunca desviadas para chave legada. */
export function isOfflineStorePurged(): boolean {
  return state.purged;
}

export function getActiveStoreUserId(): number | null {
  return state.userId;
}

export function readSession(): StoredWorkoutModeSession | null {
  return state.session;
}

export function readParked(): StoredWorkoutModeSession | null {
  return state.parked;
}

function persist(name: string, value: StoredWorkoutModeSession | null): void {
  const userId = state.userId;
  if (userId == null || !state.hydrated) return;
  void enqueueBackendTask(async () => {
    // Se o usuário mudou/purge ocorreu até aqui, não grava.
    if (state.userId !== userId || !state.hydrated) return;
    try {
      const backend = await getOfflineBackend();
      if (value) await backend.put('workout_session', makeKey(userId, name), value);
      else await backend.delete('workout_session', makeKey(userId, name));
      if (status.writeError) setStatus({ writeError: null });
    } catch (error) {
      setStatus({ writeError: error instanceof Error ? error.message : 'Falha ao gravar no aparelho.' });
    }
  });
}

export function writeSession(session: StoredWorkoutModeSession | null): void {
  state.session = session;
  persist(SESSION_NAME, session);
}

export function writeParked(session: StoredWorkoutModeSession | null): void {
  state.parked = session;
  persist(PARKED_NAME, session);
}

/** Só para testes. */
export function resetOfflineStoreForTests(): void {
  state.userId = null;
  state.hydrated = false;
  state.purged = false;
  state.session = null;
  state.parked = null;
  activating = null;
  status = { kind: null, degraded: false, persisted: null, writeError: null };
  statusListeners.clear();
}
