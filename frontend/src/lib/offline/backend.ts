// Backends de armazenamento offline atrás de uma interface mínima e
// injetável: IndexedDB (padrão), localStorage com chaves escopadas (fallback
// quando o IndexedDB não abre, ex.: modo privado antigo) e memória (testes /
// último recurso). Nenhuma dependência externa.

import { DB_NAME, DB_VERSION, STORE_NAMES, type StoreName } from './keys';

export type BackendKind = 'indexeddb' | 'localstorage' | 'memory';

export interface OfflineBackend {
  readonly kind: BackendKind;
  get(store: StoreName, key: string): Promise<unknown | undefined>;
  put(store: StoreName, key: string, value: unknown): Promise<void>;
  delete(store: StoreName, key: string): Promise<void>;
  list(store: StoreName, prefix: string): Promise<Array<{ key: string; value: unknown }>>;
  deletePrefix(store: StoreName, prefix: string): Promise<void>;
}

// ---------------------------------------------------------------------------
// Memória
// ---------------------------------------------------------------------------

export function createMemoryBackend(): OfflineBackend {
  const stores = new Map<StoreName, Map<string, unknown>>(STORE_NAMES.map((name) => [name, new Map()]));
  const clone = <T>(value: T): T => (value === undefined ? value : (JSON.parse(JSON.stringify(value)) as T));
  return {
    kind: 'memory',
    async get(store, key) {
      return clone(stores.get(store)?.get(key));
    },
    async put(store, key, value) {
      stores.get(store)?.set(key, clone(value));
    },
    async delete(store, key) {
      stores.get(store)?.delete(key);
    },
    async list(store, prefix) {
      const out: Array<{ key: string; value: unknown }> = [];
      for (const [key, value] of stores.get(store) ?? []) {
        if (key.startsWith(prefix)) out.push({ key, value: clone(value) });
      }
      return out.sort((a, b) => (a.key < b.key ? -1 : a.key > b.key ? 1 : 0));
    },
    async deletePrefix(store, prefix) {
      const map = stores.get(store);
      if (!map) return;
      for (const key of Array.from(map.keys())) if (key.startsWith(prefix)) map.delete(key);
    },
  };
}

// ---------------------------------------------------------------------------
// localStorage (fallback)
// ---------------------------------------------------------------------------

const LS_ROOT = 'musclefit.offline/';

export function createLocalStorageBackend(storage: Storage): OfflineBackend {
  const full = (store: StoreName, key: string) => `${LS_ROOT}${store}/${key}`;
  const scan = (store: StoreName, prefix: string): string[] => {
    const start = full(store, prefix);
    const keys: string[] = [];
    for (let i = 0; i < storage.length; i += 1) {
      const k = storage.key(i);
      if (k && k.startsWith(start)) keys.push(k);
    }
    return keys;
  };
  return {
    kind: 'localstorage',
    async get(store, key) {
      const raw = storage.getItem(full(store, key));
      if (raw == null) return undefined;
      try {
        return JSON.parse(raw);
      } catch {
        return undefined;
      }
    },
    async put(store, key, value) {
      storage.setItem(full(store, key), JSON.stringify(value));
    },
    async delete(store, key) {
      storage.removeItem(full(store, key));
    },
    async list(store, prefix) {
      const base = `${LS_ROOT}${store}/`;
      const out: Array<{ key: string; value: unknown }> = [];
      for (const k of scan(store, prefix).sort()) {
        try {
          out.push({ key: k.slice(base.length), value: JSON.parse(storage.getItem(k) as string) });
        } catch {
          // registro corrompido: ignora
        }
      }
      return out;
    },
    async deletePrefix(store, prefix) {
      for (const k of scan(store, prefix)) storage.removeItem(k);
    },
  };
}

// ---------------------------------------------------------------------------
// IndexedDB
// ---------------------------------------------------------------------------

/**
 * Migrações do esquema, indexadas pela versão de DESTINO. `onupgradeneeded`
 * aplica, em ordem, todas as versões entre a antiga e a nova; instalação
 * nova parte de 0. Nunca editar uma migração publicada: criar a seguinte.
 */
export const MIGRATIONS: Record<number, (db: IDBDatabase, tx: IDBTransaction) => void> = {
  1: (db) => {
    for (const name of STORE_NAMES) {
      if (!db.objectStoreNames.contains(name)) db.createObjectStore(name);
    }
  },
};

export function runMigrations(db: IDBDatabase, tx: IDBTransaction, oldVersion: number, newVersion: number): void {
  for (let version = oldVersion + 1; version <= newVersion; version += 1) {
    MIGRATIONS[version]?.(db, tx);
  }
}

function request<T>(req: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error ?? new Error('Falha no IndexedDB.'));
  });
}

function done(tx: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error ?? new Error('Falha na transação do IndexedDB.'));
    tx.onabort = () => reject(tx.error ?? new Error('Transação do IndexedDB abortada.'));
  });
}

function prefixRange(prefix: string): IDBKeyRange {
  return IDBKeyRange.bound(prefix, `${prefix}￿`);
}

/** Espera máxima por outra aba liberar o banco numa atualização de esquema (política técnica). */
export const IDB_BLOCKED_TIMEOUT_MS = 10_000;

function openDatabase(factory: IDBFactory): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    let opened: IDBOpenDBRequest;
    try {
      opened = factory.open(DB_NAME, DB_VERSION);
    } catch (error) {
      reject(error);
      return;
    }
    // Outra aba ainda segura uma versão antiga: ela recebe `versionchange` e fecha;
    // aqui só esperamos (não caímos para o localStorage escondendo os dados).
    let blocked: ReturnType<typeof setTimeout> | null = null;
    opened.onblocked = () => {
      blocked ??= setTimeout(() => reject(new Error('Abertura do IndexedDB bloqueada por outra aba.')), IDB_BLOCKED_TIMEOUT_MS);
    };
    opened.onupgradeneeded = (event) => {
      const tx = opened.transaction;
      if (tx) runMigrations(opened.result, tx, event.oldVersion, event.newVersion ?? DB_VERSION);
    };
    opened.onerror = () => {
      if (blocked) clearTimeout(blocked);
      reject(opened.error ?? new Error('Não foi possível abrir o IndexedDB.'));
    };
    opened.onsuccess = () => {
      if (blocked) clearTimeout(blocked);
      resolve(opened.result);
    };
  });
}

export async function openIndexedDbBackend(factory: IDBFactory): Promise<OfflineBackend> {
  let db = await openDatabase(factory);
  let closed = false;
  const watch = (database: IDBDatabase) => {
    // Outra aba pediu upgrade/exclusão: fecha para não bloqueá-la e reabre na próxima operação.
    database.onversionchange = () => {
      closed = true;
      database.close();
    };
    database.onclose = () => {
      closed = true;
    };
  };
  watch(db);

  async function run<T>(operation: (database: IDBDatabase) => Promise<T>): Promise<T> {
    if (closed) {
      db = await openDatabase(factory);
      closed = false;
      watch(db);
    }
    try {
      return await operation(db);
    } catch (error) {
      if ((error as { name?: string } | null)?.name !== 'InvalidStateError') throw error;
      db = await openDatabase(factory);
      closed = false;
      watch(db);
      return operation(db);
    }
  }

  return {
    kind: 'indexeddb',
    get: (store, key) =>
      run(async (database) => {
        const tx = database.transaction(store, 'readonly');
        return (await request(tx.objectStore(store).get(key))) as unknown;
      }),
    put: (store, key, value) =>
      run(async (database) => {
        const tx = database.transaction(store, 'readwrite');
        tx.objectStore(store).put(value, key);
        await done(tx);
      }),
    delete: (store, key) =>
      run(async (database) => {
        const tx = database.transaction(store, 'readwrite');
        tx.objectStore(store).delete(key);
        await done(tx);
      }),
    list: (store, prefix) =>
      run(async (database) => {
        const tx = database.transaction(store, 'readonly');
        const os = tx.objectStore(store);
        const range = prefixRange(prefix);
        const [keys, values] = await Promise.all([request(os.getAllKeys(range)), request(os.getAll(range))]);
        return keys.map((key, index) => ({ key: String(key), value: values[index] as unknown }));
      }),
    deletePrefix: (store, prefix) =>
      run(async (database) => {
        const tx = database.transaction(store, 'readwrite');
        tx.objectStore(store).delete(prefixRange(prefix));
        await done(tx);
      }),
  };
}

// ---------------------------------------------------------------------------
// Seleção
// ---------------------------------------------------------------------------

let injected: OfflineBackend | null = null;
let selecting: Promise<OfflineBackend> | null = null;

/** Só para testes: força o backend (ou volta à seleção automática com `null`). */
export function setOfflineBackendForTests(backend: OfflineBackend | null): void {
  injected = backend;
  selecting = null;
}

/**
 * IndexedDB -> localStorage escopado -> memória. `kind !== 'indexeddb'` é o
 * sinal de modo degradado (a UI avisa que os dados podem não sobreviver).
 */
export function getOfflineBackend(): Promise<OfflineBackend> {
  if (injected) return Promise.resolve(injected);
  if (!selecting) {
    selecting = (async () => {
      if (typeof indexedDB !== 'undefined') {
        try {
          return await openIndexedDbBackend(indexedDB);
        } catch {
          // cai para o fallback
        }
      }
      try {
        if (typeof window !== 'undefined' && window.localStorage) {
          const probe = `${LS_ROOT}probe`;
          window.localStorage.setItem(probe, '1');
          window.localStorage.removeItem(probe);
          return createLocalStorageBackend(window.localStorage);
        }
      } catch {
        // sem localStorage
      }
      return createMemoryBackend();
    })();
  }
  return selecting;
}
