import { describe, expect, it } from 'vitest';
import {
  NAVBAR_COLLAPSED_STORAGE_KEY,
  NAVBAR_COLLAPSED_WIDTH,
  NAVBAR_EXPANDED_WIDTH,
  browserStorage,
  isNavbarCollapsed,
  navbarToggleLabel,
  navbarWidth,
  readNavbarCollapsed,
  writeNavbarCollapsed,
  type PreferenceStorage,
} from './navbar-collapse';

function memoryStorage(initial: Record<string, string> = {}) {
  const data: Record<string, string> = { ...initial };
  const storage: PreferenceStorage = {
    getItem: (key) => (key in data ? data[key] : null),
    setItem: (key, value) => {
      data[key] = value;
    },
  };
  return { data, storage };
}

const throwing: PreferenceStorage = {
  getItem: () => {
    throw new Error('SecurityError');
  },
  setItem: () => {
    throw new Error('QuotaExceededError');
  },
};

describe('navbar-collapse', () => {
  it('usa a chave namespaced', () => {
    expect(NAVBAR_COLLAPSED_STORAGE_KEY).toBe('musclefit:navbar-collapsed');
  });

  it('default é expandido (sem storage, sem valor, valor inválido)', () => {
    expect(readNavbarCollapsed(null)).toBe(false);
    expect(readNavbarCollapsed(undefined)).toBe(false);
    expect(readNavbarCollapsed(memoryStorage().storage)).toBe(false);
    expect(
      readNavbarCollapsed(memoryStorage({ [NAVBAR_COLLAPSED_STORAGE_KEY]: 'true' }).storage),
    ).toBe(false);
  });

  it('grava e relê a preferência', () => {
    const { data, storage } = memoryStorage();
    writeNavbarCollapsed(storage, true);
    expect(data[NAVBAR_COLLAPSED_STORAGE_KEY]).toBe('1');
    expect(readNavbarCollapsed(storage)).toBe(true);
    writeNavbarCollapsed(storage, false);
    expect(data[NAVBAR_COLLAPSED_STORAGE_KEY]).toBe('0');
    expect(readNavbarCollapsed(storage)).toBe(false);
  });

  it('erros de storage não propagam', () => {
    expect(readNavbarCollapsed(throwing)).toBe(false);
    expect(() => writeNavbarCollapsed(throwing, true)).not.toThrow();
    expect(() => writeNavbarCollapsed(null, true)).not.toThrow();
  });

  it('browserStorage é null fora do navegador', () => {
    expect(browserStorage()).toBeNull();
  });

  it('recolhido só vale no desktop', () => {
    expect(isNavbarCollapsed(true, true)).toBe(true);
    expect(isNavbarCollapsed(true, false)).toBe(false);
    expect(isNavbarCollapsed(false, true)).toBe(false);
  });

  it('largura acompanha o estado', () => {
    expect(navbarWidth(false)).toBe(NAVBAR_EXPANDED_WIDTH);
    expect(navbarWidth(true)).toBe(NAVBAR_COLLAPSED_WIDTH);
    expect(NAVBAR_COLLAPSED_WIDTH).toBeGreaterThanOrEqual(64);
    expect(NAVBAR_COLLAPSED_WIDTH).toBeLessThanOrEqual(72);
  });

  it('rótulo do botão descreve a ação', () => {
    expect(navbarToggleLabel(false)).toBe('Recolher menu');
    expect(navbarToggleLabel(true)).toBe('Expandir menu');
  });
});
