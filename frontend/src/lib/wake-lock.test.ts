import { describe, expect, it } from 'vitest';
import { createWakeLockController, wakeLockNotice } from './wake-lock';

function setup(options: { supported?: boolean; failFirst?: boolean } = {}) {
  let visible = true;
  const visibilityListeners = new Set<() => void>();
  let requests = 0;
  const sentinels: Array<{ released: boolean; release: () => Promise<void>; fire: () => void; listeners: Array<() => void> }> = [];
  const api =
    options.supported === false
      ? null
      : {
          request: async () => {
            requests += 1;
            if (options.failFirst && requests === 1) throw new Error('NotAllowedError');
            const sentinel = {
              released: false,
              listeners: [] as Array<() => void>,
              release: async () => {
                sentinel.released = true;
              },
              addEventListener: (_t: 'release', cb: () => void) => void sentinel.listeners.push(cb),
              fire: () => sentinel.listeners.forEach((cb) => cb()),
            };
            sentinels.push(sentinel);
            return sentinel;
          },
        };
  const controller = createWakeLockController({
    api: () => api,
    isVisible: () => visible,
    addVisibilityListener: (cb) => {
      visibilityListeners.add(cb);
      return () => visibilityListeners.delete(cb);
    },
  });
  return {
    controller,
    sentinels,
    requests: () => requests,
    setVisible(v: boolean) {
      visible = v;
      visibilityListeners.forEach((cb) => cb());
    },
  };
}

describe('wake lock', () => {
  it('sem suporte: estado unsupported e aviso de texto, sem lançar', async () => {
    const t = setup({ supported: false });
    await t.controller.enable();
    expect(t.controller.getState()).toBe('unsupported');
    expect(wakeLockNotice(t.controller.getState())).toContain('tela pode apagar');
  });

  it('falha ao pedir: estado failed silencioso com aviso; sem aviso quando ativo', async () => {
    const t = setup({ failFirst: true });
    await t.controller.enable();
    expect(t.controller.getState()).toBe('failed');
    expect(wakeLockNotice('failed')).not.toBeNull();
    t.setVisible(true); // voltar ao primeiro plano tenta de novo
    await Promise.resolve();
    await Promise.resolve();
    expect(t.controller.getState()).toBe('active');
    expect(wakeLockNotice('active')).toBeNull();
  });

  it('perda do lock (página oculta) e reaquisição ao voltar a ficar visível', async () => {
    const t = setup();
    await t.controller.enable();
    expect(t.controller.getState()).toBe('active');
    t.setVisible(false);
    t.sentinels[0].fire(); // o navegador libera o lock ao ocultar
    expect(t.controller.getState()).toBe('inactive');
    t.setVisible(true);
    await Promise.resolve();
    await Promise.resolve();
    expect(t.requests()).toBe(2);
    expect(t.controller.getState()).toBe('active');
  });

  it('disable libera o lock e para de readquirir', async () => {
    const t = setup();
    await t.controller.enable();
    await t.controller.disable();
    expect(t.sentinels[0].released).toBe(true);
    t.setVisible(true);
    await Promise.resolve();
    expect(t.requests()).toBe(1);
    expect(t.controller.getState()).toBe('inactive');
  });
});
