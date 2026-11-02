import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  ACTIVE_WORK_PROVIDER_TIMEOUT_MS,
  hasActiveWork,
  registerActiveWorkProvider,
} from './active-work';

const offs: Array<() => void> = [];
function reg(fn: () => boolean | Promise<boolean>) {
  const off = registerActiveWorkProvider(fn);
  offs.push(off);
  return off;
}
afterEach(() => {
  offs.splice(0).forEach((off) => off());
  vi.useRealTimers();
});

describe('hasActiveWork', () => {
  it('sem provedor: false', async () => {
    expect(await hasActiveWork()).toBe(false);
  });
  it('usa o provedor (síncrono e assíncrono)', async () => {
    const off = reg(() => true);
    expect(await hasActiveWork()).toBe(true);
    off();
    reg(async () => false);
    expect(await hasActiveWork()).toBe(false);
  });
  it('vários provedores: OR de todos', async () => {
    reg(() => false);
    reg(async () => false);
    expect(await hasActiveWork()).toBe(false);
    reg(async () => true);
    expect(await hasActiveWork()).toBe(true);
  });
  it('falha de qualquer provedor conta como trabalho ativo', async () => {
    reg(() => false);
    const off = reg(() => {
      throw new Error('idb indisponível');
    });
    expect(await hasActiveWork()).toBe(true);
    off();
    reg(() => Promise.reject(new Error('x')));
    expect(await hasActiveWork()).toBe(true);
  });
  it('o cancelamento só remove o próprio provedor', async () => {
    const off1 = reg(() => true);
    reg(() => false);
    off1();
    expect(await hasActiveWork()).toBe(false);
  });
  it('provedor que não responde no prazo conta como trabalho ativo', async () => {
    vi.useFakeTimers();
    reg(() => new Promise<boolean>(() => {}));
    const result = hasActiveWork();
    await vi.advanceTimersByTimeAsync(ACTIVE_WORK_PROVIDER_TIMEOUT_MS + 1);
    expect(await result).toBe(true);
  });
});
