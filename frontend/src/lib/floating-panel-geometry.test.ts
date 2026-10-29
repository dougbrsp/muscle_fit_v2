import { describe, expect, it } from 'vitest';
import {
  APP_HEADER_HEIGHT,
  clampPosition,
  defaultPosition,
  FLOATING_PANEL_MARGIN,
  FLOATING_PANELS_STORAGE_KEY,
  parseFloatingPanels,
  readFloatingPanels,
  writeFloatingPanels,
} from './floating-panel-geometry';

const viewport = { width: 1440, height: 900 };
const size = { width: 300, height: 200 };

describe('clampPosition', () => {
  it('mantém a janela dentro da viewport e abaixo do header', () => {
    expect(clampPosition({ x: -50, y: 0 }, size, viewport)).toEqual({
      x: FLOATING_PANEL_MARGIN,
      y: APP_HEADER_HEIGHT + FLOATING_PANEL_MARGIN,
    });
    expect(clampPosition({ x: 5000, y: 5000 }, size, viewport)).toEqual({
      x: 1440 - 300 - FLOATING_PANEL_MARGIN,
      y: 900 - 200 - FLOATING_PANEL_MARGIN,
    });
    expect(clampPosition({ x: 400.4, y: 300.6 }, size, viewport)).toEqual({ x: 400, y: 301 });
  });

  it('janela maior que a viewport gruda no canto superior esquerdo (título visível)', () => {
    expect(clampPosition({ x: 100, y: 500 }, { width: 2000, height: 2000 }, viewport)).toEqual({
      x: FLOATING_PANEL_MARGIN,
      y: APP_HEADER_HEIGHT + FLOATING_PANEL_MARGIN,
    });
  });
});

describe('defaultPosition', () => {
  it('encosta à direita, abaixo do header, uma ao lado da outra', () => {
    const first = defaultPosition(0, size, viewport);
    const second = defaultPosition(1, size, viewport);
    expect(first).toEqual({ x: 1440 - 316, y: APP_HEADER_HEIGHT + 16 });
    expect(second.x).toBe(first.x - 316);
    expect(second.y).toBe(first.y);
  });
});

describe('persistência', () => {
  function memoryStorage() {
    let value: string | null = null;
    return {
      getItem: (key: string) => (key === FLOATING_PANELS_STORAGE_KEY ? value : null),
      setItem: (_key: string, next: string) => {
        value = next;
      },
    };
  }

  it('grava e lê de volta', () => {
    const storage = memoryStorage();
    writeFloatingPanels({ a: { position: { x: 10, y: 90 }, minimized: true, closed: false } }, storage);
    expect(readFloatingPanels(storage)).toEqual({ a: { position: { x: 10, y: 90 }, minimized: true, closed: false } });
  });

  it('JSON inválido ou fora do formato vira estado vazio/saneado', () => {
    expect(parseFloatingPanels('{quebrado')).toEqual({});
    expect(parseFloatingPanels('[1,2]')).toEqual({});
    expect(parseFloatingPanels('{"a":{"position":{"x":"1","y":2},"closed":"sim"},"b":3}')).toEqual({
      a: { position: null, minimized: false, closed: false },
    });
  });

  it('storage que lança ou ausente não quebra', () => {
    const broken = {
      getItem: () => {
        throw new Error('bloqueado');
      },
      setItem: () => {
        throw new Error('cota');
      },
    };
    expect(readFloatingPanels(broken)).toEqual({});
    expect(() => writeFloatingPanels({}, broken)).not.toThrow();
    expect(readFloatingPanels(null)).toEqual({});
  });
});
