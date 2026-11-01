import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  MOBILE_UI_STORAGE_KEY,
  applyQueryOverride,
  useMobileUi,
  clearMobileUiOverride,
  isMobileUiEnabled,
  parseBuildFlag,
  parseQueryOverride,
  parseStoredOverride,
  resolveMobileUi,
} from './flags';

function fakeWindow(opts: { search?: string; stored?: string | null; throwing?: boolean }) {
  const store = new Map<string, string>();
  if (opts.stored != null) store.set(MOBILE_UI_STORAGE_KEY, opts.stored);
  const localStorage = {
    getItem: (k: string) => {
      if (opts.throwing) throw new Error('blocked');
      return store.get(k) ?? null;
    },
    setItem: (k: string, v: string) => {
      if (opts.throwing) throw new Error('blocked');
      store.set(k, v);
    },
    removeItem: (k: string) => {
      if (opts.throwing) throw new Error('blocked');
      store.delete(k);
    },
  };
  vi.stubGlobal('window', { location: { search: opts.search ?? '' }, localStorage });
  return store;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe('parse*', () => {
  it('build flag: default OFF e valores desconhecidos = OFF', () => {
    expect(parseBuildFlag(undefined)).toBe(false);
    expect(parseBuildFlag('')).toBe(false);
    expect(parseBuildFlag('talvez')).toBe(false);
    expect(parseBuildFlag('0')).toBe(false);
    expect(parseBuildFlag('1')).toBe(true);
    expect(parseBuildFlag(' TRUE ')).toBe(true);
  });
  it('override salvo: só on/off', () => {
    expect(parseStoredOverride('on')).toBe('on');
    expect(parseStoredOverride('off')).toBe('off');
    expect(parseStoredOverride('1')).toBeNull();
    expect(parseStoredOverride(null)).toBeNull();
  });
  it('query: só 0 e 1', () => {
    expect(parseQueryOverride('?mobile=1')).toBe('on');
    expect(parseQueryOverride('?a=b&mobile=0')).toBe('off');
    expect(parseQueryOverride('?mobile=2')).toBeNull();
    expect(parseQueryOverride('')).toBeNull();
    expect(parseQueryOverride(undefined)).toBeNull();
  });
});

describe('resolveMobileUi (precedência)', () => {
  it('query > storage > build', () => {
    expect(resolveMobileUi(true, 'off', 'on')).toBe(true);
    expect(resolveMobileUi(false, 'on', 'off')).toBe(false);
    expect(resolveMobileUi(true, 'off', null)).toBe(false);
    expect(resolveMobileUi(false, 'on', null)).toBe(true);
    expect(resolveMobileUi(true, null, null)).toBe(true);
    expect(resolveMobileUi(false, null, null)).toBe(false);
  });
});

describe('isMobileUiEnabled', () => {
  it('servidor (sem window): usa só o build e default é OFF', () => {
    expect(isMobileUiEnabled()).toBe(false);
    vi.stubEnv('NEXT_PUBLIC_MOBILE_UI', '1');
    expect(isMobileUiEnabled()).toBe(true);
  });
  it('sem nada: desligada', () => {
    fakeWindow({});
    expect(isMobileUiEnabled()).toBe(false);
  });
  it('override salvo liga/desliga sobre o build', () => {
    fakeWindow({ stored: 'on' });
    expect(isMobileUiEnabled()).toBe(true);
    vi.stubEnv('NEXT_PUBLIC_MOBILE_UI', '1');
    fakeWindow({ stored: 'off' });
    expect(isMobileUiEnabled()).toBe(false);
  });
  it('query vence o salvo e a leitura NAO grava', () => {
    const store = fakeWindow({ search: '?mobile=1', stored: 'off' });
    expect(isMobileUiEnabled()).toBe(true);
    expect(store.get(MOBILE_UI_STORAGE_KEY)).toBe('off');
  });
  it('applyQueryOverride persiste ?mobile=0|1 e ignora o resto', () => {
    let store = fakeWindow({ search: '?mobile=1' });
    applyQueryOverride();
    expect(store.get(MOBILE_UI_STORAGE_KEY)).toBe('on');
    store = fakeWindow({ search: '?mobile=0', stored: 'on' });
    applyQueryOverride();
    expect(store.get(MOBILE_UI_STORAGE_KEY)).toBe('off');
    store = fakeWindow({ search: '?mobile=9' });
    applyQueryOverride();
    expect(store.has(MOBILE_UI_STORAGE_KEY)).toBe(false);
    fakeWindow({ throwing: true, search: '?mobile=1' });
    expect(() => applyQueryOverride()).not.toThrow();
  });
  it('useMobileUi devolve null antes de montar', () => {
    function Probe() {
      return createElement('span', null, String(useMobileUi()));
    }
    expect(renderToStaticMarkup(createElement(Probe))).toBe('<span>null</span>');
  });
  it('storage bloqueado não quebra; query ainda vale na leitura', () => {
    fakeWindow({ throwing: true });
    expect(isMobileUiEnabled()).toBe(false);
    fakeWindow({ throwing: true, search: '?mobile=1' });
    expect(isMobileUiEnabled()).toBe(true);
    expect(() => clearMobileUiOverride()).not.toThrow();
  });
  it('clearMobileUiOverride volta ao build', () => {
    const store = fakeWindow({ stored: 'on' });
    clearMobileUiOverride();
    expect(store.has(MOBILE_UI_STORAGE_KEY)).toBe(false);
    expect(isMobileUiEnabled()).toBe(false);
  });
});
