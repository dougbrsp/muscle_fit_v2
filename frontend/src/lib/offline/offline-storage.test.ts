import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { hasActiveWork, registerActiveWorkProvider } from '../mobile/active-work';
import {
  createLocalStorageBackend,
  createMemoryBackend,
  MIGRATIONS,
  runMigrations,
  setOfflineBackendForTests,
  type OfflineBackend,
} from './backend';
import { offlineHasActiveWork } from './active-work';
import { ACTIVE_USER_MARKER_KEY, DB_VERSION, LEGACY_PARKED_KEY, LEGACY_SESSION_KEY, STORE_NAMES, makeKey } from './keys';
import type { OfflineOp } from './op-types';
import { countUnsyncedOps, purgeOfflineData } from './purge';
import { flushBackendTasks, resolveKnownUserId, setActiveUserId } from './scope';
import { activateOfflineUser, getStorageStatus, resetOfflineStoreForTests } from './session-store';
import {
  clearStoredSession,
  loadParkedSession,
  loadStoredSession,
  parkStoredSession,
  saveStoredSession,
} from '../workout-mode-storage';
import type { StoredWorkoutModeSession } from '../workout-mode-types';

function installWindow(): Map<string, string> {
  const store = new Map<string, string>();
  const localStorage = {
    getItem: (k: string) => (store.has(k) ? (store.get(k) as string) : null),
    setItem: (k: string, v: string) => void store.set(k, String(v)),
    removeItem: (k: string) => void store.delete(k),
    key: (i: number) => Array.from(store.keys())[i] ?? null,
    get length() {
      return store.size;
    },
  };
  (globalThis as unknown as { window: unknown }).window = { localStorage };
  return store;
}

const SESSION_A: StoredWorkoutModeSession = { session_id: 10, source: 'plan', workout_plan_id: 3 };
const SESSION_B: StoredWorkoutModeSession = { session_id: 20, source: 'freeform' };

function op(overrides: Partial<OfflineOp>): OfflineOp {
  return {
    opId: 'op-1',
    userId: 1,
    kind: 'addSet',
    sessionId: 10,
    exerciseId: 5,
    payload: {},
    clientKey: 'k1',
    createdAt: 1,
    attempts: 0,
    status: 'saved_local',
    ...overrides,
  };
}

let ls: Map<string, string>;
let backend: OfflineBackend;

beforeEach(() => {
  ls = installWindow();
  backend = createMemoryBackend();
  setOfflineBackendForTests(backend);
  resetOfflineStoreForTests();
  setActiveUserId(null);
});

afterEach(async () => {
  await flushBackendTasks();
  setOfflineBackendForTests(null);
  delete (globalThis as unknown as { window?: unknown }).window;
});

describe('backends', () => {
  it('memória e localStorage obedecem ao mesmo contrato (get/put/list/deletePrefix)', async () => {
    const memory = createMemoryBackend();
    const lsBackend = createLocalStorageBackend((globalThis as unknown as { window: { localStorage: Storage } }).window.localStorage);
    for (const b of [memory, lsBackend]) {
      await b.put('meta', 'v1:1:a', { x: 1 });
      await b.put('meta', 'v1:1:b', { x: 2 });
      await b.put('meta', 'v1:12:a', { x: 3 });
      expect(await b.get('meta', 'v1:1:a')).toEqual({ x: 1 });
      expect((await b.list('meta', 'v1:1:')).map((r) => r.key)).toEqual(['v1:1:a', 'v1:1:b']);
      await b.deletePrefix('meta', 'v1:1:');
      expect(await b.get('meta', 'v1:1:a')).toBeUndefined();
      expect(await b.get('meta', 'v1:12:a')).toEqual({ x: 3 });
    }
  });

  it('migrações: instalação nova roda da 0 até DB_VERSION e cria todos os stores', () => {
    const created: string[] = [];
    const db = {
      objectStoreNames: { contains: (name: string) => created.includes(name) },
      createObjectStore: (name: string) => void created.push(name),
    } as unknown as IDBDatabase;
    runMigrations(db, {} as IDBTransaction, 0, DB_VERSION);
    expect(created).toEqual([...STORE_NAMES]);
    expect(Object.keys(MIGRATIONS).map(Number).sort()).toEqual(Array.from({ length: DB_VERSION }, (_, i) => i + 1));
  });

  it('migrações: banco já na versão atual não recria nada', () => {
    const created: string[] = [];
    const db = { createObjectStore: (n: string) => void created.push(n) } as unknown as IDBDatabase;
    runMigrations(db, {} as IDBTransaction, DB_VERSION, DB_VERSION);
    expect(created).toEqual([]);
  });
});

describe('fachada workout-mode-storage com usuário ativo', () => {
  it('grava no backend escopado por usuário e relê do espelho', async () => {
    await activateOfflineUser(1);
    saveStoredSession(SESSION_A);
    expect(loadStoredSession()).toEqual(SESSION_A);
    await flushBackendTasks();
    expect(await backend.get('workout_session', makeKey(1, 'session'))).toEqual(SESSION_A);
    expect(ls.has('musclefit.workout-mode-session')).toBe(false);
  });

  it('sem leitura cruzada: outro usuário não vê a sessão do primeiro, e a troca de conta limpa a anterior', async () => {
    await activateOfflineUser(1);
    saveStoredSession(SESSION_A);
    await flushBackendTasks();
    resetOfflineStoreForTests();
    await activateOfflineUser(2, { verified: true });
    expect(loadStoredSession()).toBeNull();
    saveStoredSession(SESSION_B);
    await flushBackendTasks();
    expect(await backend.get('workout_session', makeKey(1, 'session'))).toBeUndefined();
    expect(await backend.get('workout_session', makeKey(2, 'session'))).toEqual(SESSION_B);
  });

  it('reativar o mesmo usuário (rotação/remontagem) não reidrata nem apaga o espelho', async () => {
    await activateOfflineUser(1);
    saveStoredSession(SESSION_A);
    await activateOfflineUser(1);
    expect(loadStoredSession()).toEqual(SESSION_A);
  });

  it('park/clear: estaciona a sessão e limpa o slot ativo', async () => {
    await activateOfflineUser(1);
    saveStoredSession(SESSION_A);
    parkStoredSession();
    expect(loadStoredSession()).toBeNull();
    expect(loadParkedSession()).toEqual(SESSION_A);
    saveStoredSession(SESSION_B);
    clearStoredSession();
    expect(loadStoredSession()).toBeNull();
    await flushBackendTasks();
    expect(await backend.get('workout_session', makeKey(1, 'session'))).toBeUndefined();
    expect(await backend.get('workout_session', makeKey(1, 'parked'))).toEqual(SESSION_A);
  });

  it('IndexedDB indisponível (backend em memória/localStorage) marca modo degradado', async () => {
    await activateOfflineUser(1);
    expect(getStorageStatus().degraded).toBe(true);
    expect(getStorageStatus().kind).toBe('memory');
  });
});

describe('migração do localStorage legado', () => {
  it('importa para o usuário só com prova de posse e remove a chave antiga', async () => {
    ls.set(LEGACY_SESSION_KEY, JSON.stringify(SESSION_A));
    ls.set(LEGACY_PARKED_KEY, JSON.stringify(SESSION_B));
    await activateOfflineUser(1, { validateLegacySession: async () => 'owned' });
    expect(loadStoredSession()).toEqual(SESSION_A);
    expect(loadParkedSession()).toEqual(SESSION_B);
    expect(ls.has(LEGACY_SESSION_KEY)).toBe(false);
    expect(ls.has(LEGACY_PARKED_KEY)).toBe(false);
    expect(await backend.get('workout_session', makeKey(1, 'session'))).toEqual(SESSION_A);
  });

  it('sessão de outra conta (404/403) é descartada, não importada', async () => {
    ls.set(LEGACY_SESSION_KEY, JSON.stringify(SESSION_A));
    await activateOfflineUser(2, { validateLegacySession: async () => 'foreign' });
    expect(loadStoredSession()).toBeNull();
    expect(ls.has(LEGACY_SESSION_KEY)).toBe(false);
  });

  it('sem validação disponível, não atribui a ninguém e também não apaga a chave legada', async () => {
    ls.set(LEGACY_SESSION_KEY, JSON.stringify(SESSION_A));
    await activateOfflineUser(1);
    expect(loadStoredSession()).toBeNull();
    expect(ls.has(LEGACY_SESSION_KEY)).toBe(true);
  });

  it('erro de rede na validação mantém a chave legada para nova tentativa', async () => {
    ls.set(LEGACY_SESSION_KEY, JSON.stringify(SESSION_A));
    await activateOfflineUser(1, { validateLegacySession: async () => 'unknown' });
    expect(loadStoredSession()).toBeNull();
    expect(ls.has(LEGACY_SESSION_KEY)).toBe(true);
  });

  it('slot do usuário já ocupado vence o legado', async () => {
    await backend.put('workout_session', makeKey(1, 'session'), SESSION_B);
    ls.set(LEGACY_SESSION_KEY, JSON.stringify(SESSION_A));
    await activateOfflineUser(1, { validateLegacySession: async () => 'owned' });
    expect(loadStoredSession()).toEqual(SESSION_B);
    expect(ls.has(LEGACY_SESSION_KEY)).toBe(false);
  });
});

describe('purge e logout', () => {
  it('logout apaga a partição do usuário, chaves legadas e o marcador; outro usuário intacto', async () => {
    await backend.put('workout_session', makeKey(2, 'session'), SESSION_B);
    await activateOfflineUser(1);
    saveStoredSession(SESSION_A);
    await backend.put('op_queue', makeKey(1, 'op:x'), op({ status: 'synced' }));
    ls.set(LEGACY_SESSION_KEY, JSON.stringify(SESSION_B));
    await purgeOfflineData();
    expect(await backend.list('workout_session', makeKey(1, ''))).toEqual([]);
    expect(await backend.list('op_queue', makeKey(1, ''))).toEqual([]);
    expect(await backend.get('workout_session', makeKey(2, 'session'))).toEqual(SESSION_B);
    expect(ls.has(LEGACY_SESSION_KEY)).toBe(false);
    expect(ls.has(ACTIVE_USER_MARKER_KEY)).toBe(false);
    expect(resolveKnownUserId()).toBeNull();
  });

  it('depois do purge, gravações tardias são descartadas e não vão para a chave legada', async () => {
    await activateOfflineUser(1);
    await purgeOfflineData();
    saveStoredSession(SESSION_A);
    await flushBackendTasks();
    expect(loadStoredSession()).toBeNull();
    expect(ls.has(LEGACY_SESSION_KEY)).toBe(false);
    expect(await backend.list('workout_session', makeKey(1, ''))).toEqual([]);
  });

  it('escrita enfileirada antes do purge não reaparece depois dele', async () => {
    await activateOfflineUser(1);
    saveStoredSession(SESSION_A);
    await purgeOfflineData();
    await flushBackendTasks();
    expect(await backend.list('workout_session', makeKey(1, ''))).toEqual([]);
  });

  it('login confirmado de OUTRA conta apaga tudo da anterior, inclusive ops não sincronizadas', async () => {
    await activateOfflineUser(1);
    saveStoredSession(SESSION_A);
    await backend.put('op_queue', makeKey(1, 'op:a'), op({ opId: 'a', status: 'error' }));
    await flushBackendTasks();
    resetOfflineStoreForTests(); // recarregou a página: marcador do usuário 1 continua
    await activateOfflineUser(2, { verified: true });
    await flushBackendTasks();
    expect(loadStoredSession()).toBeNull();
    expect(await backend.get('workout_session', makeKey(1, 'session'))).toBeUndefined();
    expect(await countUnsyncedOps(1)).toBe(0);
    expect(await countUnsyncedOps(2)).toBe(0);
  });

  it('401 (keepUnsynced) apaga a sessão mas preserva ops não sincronizadas, isoladas por usuário', async () => {
    await activateOfflineUser(1);
    saveStoredSession(SESSION_A);
    await backend.put('op_queue', makeKey(1, 'op:a'), op({ opId: 'a', status: 'error' }));
    await backend.put('op_queue', makeKey(1, 'op:b'), op({ opId: 'b', status: 'synced' }));
    await purgeOfflineData({ keepUnsynced: true });
    expect(await backend.list('workout_session', makeKey(1, ''))).toEqual([]);
    const left = await backend.list('op_queue', makeKey(1, ''));
    expect(left.map((r) => (r.value as OfflineOp).opId)).toEqual(['a']);
    expect(await countUnsyncedOps(1)).toBe(1);
    expect(await countUnsyncedOps(2)).toBe(0);
    expect(ls.get(ACTIVE_USER_MARKER_KEY)).toBe('1');
  });

  it('contagem de não sincronizadas inclui saved_local, syncing, error e conflict; exclui synced', async () => {
    const statuses = ['saved_local', 'syncing', 'error', 'conflict', 'synced'] as const;
    for (const [i, status] of statuses.entries()) {
      await backend.put('op_queue', makeKey(1, `op:${i}`), op({ opId: String(i), status }));
    }
    expect(await countUnsyncedOps(1)).toBe(4);
  });
});

describe('hasActiveWork (contrato da Frente C)', () => {
  it('sem usuário conhecido: false', async () => {
    expect(await offlineHasActiveWork()).toBe(false);
  });

  it('sessão local em andamento: true', async () => {
    await activateOfflineUser(1);
    saveStoredSession(SESSION_A);
    expect(await offlineHasActiveWork()).toBe(true);
  });

  it('sem sessão mas com op pendente: true; só ops sincronizadas: false', async () => {
    setActiveUserId(1);
    await backend.put('op_queue', makeKey(1, 'op:a'), op({ status: 'synced' }));
    expect(await offlineHasActiveWork()).toBe(false);
    await backend.put('op_queue', makeKey(1, 'op:b'), op({ opId: 'b', status: 'saved_local' }));
    expect(await offlineHasActiveWork()).toBe(true);
  });

  it('lê do armazenamento mesmo sem a tela de treino montada (outra página)', async () => {
    await backend.put('workout_session', makeKey(1, 'session'), SESSION_A);
    setActiveUserId(1);
    expect(await offlineHasActiveWork()).toBe(true);
  });

  it('integra com o registro do provedor: erro do provedor vira true', async () => {
    const off = registerActiveWorkProvider(() => offlineHasActiveWork());
    expect(await hasActiveWork()).toBe(false);
    setOfflineBackendForTests({
      ...backend,
      get: async () => {
        throw new Error('falha');
      },
    });
    setActiveUserId(1);
    expect(await hasActiveWork()).toBe(true);
    off();
  });
});


describe('último usuário conhecido para abrir offline', () => {
  it('vale até 7 dias depois do /auth/me 200 e some por ops órfãs ou purge', async () => {
    const { knownUserForOffline, KNOWN_USER_MAX_AGE_MS } = await import('./scope');
    const { ORPHAN_FLAG_KEY } = await import('./keys');
    const t0 = 1_000_000_000_000;
    expect(knownUserForOffline(t0)).toBeNull(); // nunca confirmado
    await activateOfflineUser(1, { verified: true });
    const verifiedAt = Number(ls.get('musclefit.offline.last-verified'));
    expect(knownUserForOffline(verifiedAt + 1000)).toBe(1);
    expect(knownUserForOffline(verifiedAt + KNOWN_USER_MAX_AGE_MS + 1)).toBeNull();
    ls.set(ORPHAN_FLAG_KEY, '1');
    expect(knownUserForOffline(verifiedAt + 1000)).toBeNull();
  });

  it('ativação sem verificação (fallback offline) NÃO renova a validade', async () => {
    await activateOfflineUser(1, { verified: true });
    const first = ls.get('musclefit.offline.last-verified');
    resetOfflineStoreForTests();
    await activateOfflineUser(1);
    expect(ls.get('musclefit.offline.last-verified')).toBe(first);
  });

  it('401 (keepUnsynced) com ops pendentes marca o marcador como órfão; logout completo limpa tudo', async () => {
    const { knownUserForOffline } = await import('./scope');
    await activateOfflineUser(1, { verified: true });
    await backend.put('op_queue', makeKey(1, 'op:a'), op({ opId: 'a', status: 'error' }));
    await purgeOfflineData({ keepUnsynced: true });
    expect(knownUserForOffline()).toBeNull();
    expect(ls.get('musclefit.offline.orphan-ops')).toBe('1');
    await purgeOfflineData();
    expect(ls.has('musclefit.offline.orphan-ops')).toBe(false);
    expect(ls.has('musclefit.offline.last-verified')).toBe(false);
  });
});


describe('chaves de exercício na sessão local', () => {
  it('exercise_client_keys sobrevive ao reload (remontagem reaproveita a mesma client_key)', async () => {
    await activateOfflineUser(1);
    saveStoredSession({ ...SESSION_A, exercise_client_keys: { '10#7002': 'key-abc' } });
    await flushBackendTasks();
    resetOfflineStoreForTests();
    await activateOfflineUser(1);
    expect(loadStoredSession()?.exercise_client_keys).toEqual({ '10#7002': 'key-abc' });
  });
});
