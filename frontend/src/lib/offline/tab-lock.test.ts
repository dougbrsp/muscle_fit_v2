import { afterEach, describe, expect, it } from 'vitest';
import { acquireWriterLock, resetWriterLockForTests } from './tab-lock';

function setNavigator(value: unknown) {
  Object.defineProperty(globalThis, 'navigator', { value, configurable: true, writable: true });
}

// Web Locks mínimo e exclusivo: nome -> quem segura; pedidos esperam em fila.
function installLocks() {
  const held = new Set<string>();
  const waiting = new Map<string, Array<() => void>>();
  const locks = {
    request(name: string, options: { ifAvailable?: boolean }, callback: (lock: unknown) => unknown) {
      if (!held.has(name)) {
        held.add(name);
        return Promise.resolve(callback({ name }));
      }
      if (options.ifAvailable) return Promise.resolve(callback(null));
      return new Promise((resolve) => {
        const queue = waiting.get(name) ?? [];
        queue.push(() => {
          held.add(name);
          resolve(callback({ name }));
        });
        waiting.set(name, queue);
      });
    },
    release(name: string) {
      held.delete(name);
      waiting.get(name)?.shift()?.();
    },
  };
  setNavigator({ locks });
  return locks;
}

afterEach(() => {
  resetWriterLockForTests();
  setNavigator(undefined);
});

describe('único escritor entre abas', () => {
  it('sem Web Locks: segue como dona (sem proteção entre abas)', async () => {
    setNavigator({});
    expect((await acquireWriterLock(1)).role).toBe('owner');
  });

  it('a 1ª aba é dona; a 2ª fica seguidora e vira dona quando a 1ª sai', async () => {
    const locks = installLocks();
    const first = await acquireWriterLock(1);
    expect(first.role).toBe('owner');
    resetWriterLockForTests(); // simula OUTRA aba (estado de módulo próprio)
    const second = await acquireWriterLock(1);
    expect(second.role).toBe('follower');
    let owner = false;
    void second.whenOwner.then(() => (owner = true));
    await Promise.resolve();
    expect(owner).toBe(false);
    locks.release('musclefit-offline-1');
    await second.whenOwner;
    expect(owner).toBe(true);
  });

  it('usuários diferentes não se bloqueiam', async () => {
    installLocks();
    expect((await acquireWriterLock(1)).role).toBe('owner');
    resetWriterLockForTests();
    expect((await acquireWriterLock(2)).role).toBe('owner');
  });

  it('a mesma aba pedindo de novo continua dona (idempotente)', async () => {
    installLocks();
    await acquireWriterLock(1);
    expect((await acquireWriterLock(1)).role).toBe('owner');
  });
});
