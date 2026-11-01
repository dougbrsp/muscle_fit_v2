import { afterEach, describe, expect, it } from 'vitest';
import { hasActiveWork, registerActiveWorkProvider } from './active-work';

let unregister: (() => void) | null = null;
afterEach(() => {
  unregister?.();
  unregister = null;
});

describe('hasActiveWork', () => {
  it('sem provedor: false', async () => {
    expect(await hasActiveWork()).toBe(false);
  });
  it('usa o provedor (síncrono e assíncrono)', async () => {
    unregister = registerActiveWorkProvider(() => true);
    expect(await hasActiveWork()).toBe(true);
    unregister();
    unregister = registerActiveWorkProvider(async () => false);
    expect(await hasActiveWork()).toBe(false);
  });
  it('falha do provedor conta como trabalho ativo', async () => {
    unregister = registerActiveWorkProvider(() => {
      throw new Error('idb indisponível');
    });
    expect(await hasActiveWork()).toBe(true);
    unregister();
    unregister = registerActiveWorkProvider(() => Promise.reject(new Error('x')));
    expect(await hasActiveWork()).toBe(true);
  });
  it('o cancelamento só remove o próprio provedor', async () => {
    const off1 = registerActiveWorkProvider(() => true);
    unregister = registerActiveWorkProvider(() => false);
    off1();
    expect(await hasActiveWork()).toBe(false);
    unregister();
    unregister = null;
    expect(await hasActiveWork()).toBe(false);
  });
});
