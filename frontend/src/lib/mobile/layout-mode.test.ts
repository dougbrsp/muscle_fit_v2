import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import {
  MOBILE_BREAKPOINT_PX,
  MOBILE_MEDIA_QUERY,
  MOBILE_PHONE_QUERY,
  MOBILE_PORTRAIT_QUERY,
  layoutModeFromMatch,
  subscribeMediaQuery,
  useLayoutMode,
} from './layout-mode';

describe('layout-mode', () => {
  it('breakpoint alinhado ao sm do Mantine (48em = 768px)', () => {
    expect(MOBILE_BREAKPOINT_PX).toBe(768);
    expect(MOBILE_PORTRAIT_QUERY).toBe('(max-width: 47.99em)');
  });
  it('D2: celular = toque e menor lado da TELA < 500 px (sem depender da janela)', () => {
    expect(MOBILE_PHONE_QUERY).toBe(
      '(pointer: coarse) and (max-device-width: 499.9px), (pointer: coarse) and (max-device-height: 499.9px)',
    );
    expect(MOBILE_MEDIA_QUERY).toBe(`${MOBILE_PORTRAIT_QUERY}, ${MOBILE_PHONE_QUERY}`);
    // sem altura/largura da janela (teclado e barras mudam) e sem User-Agent
    expect(MOBILE_PHONE_QUERY).not.toMatch(/(?<!device-)(max|min)-(width|height)/);
    expect(MOBILE_MEDIA_QUERY.toLowerCase()).not.toContain('user-agent');
  });
  it('mapeia o resultado do matchMedia', () => {
    expect(layoutModeFromMatch(true)).toBe('mobile');
    expect(layoutModeFromMatch(false)).toBe('desktop');
  });
  it('antes de montar devolve null (sem hydration mismatch)', () => {
    function Probe() {
      return createElement('span', null, String(useLayoutMode()));
    }
    expect(renderToStaticMarkup(createElement(Probe))).toBe('<span>null</span>');
  });

  describe('subscribeMediaQuery', () => {
    it('modo moderno: emite o valor atual, reage a change e limpa no unmount', () => {
      let handler: (() => void) | undefined;
      const mql = {
        matches: false,
        addEventListener: vi.fn((_: string, h: () => void) => {
          handler = h;
        }),
        removeEventListener: vi.fn(),
      };
      const seen: boolean[] = [];
      const off = subscribeMediaQuery(mql, (m) => seen.push(m));
      expect(seen).toEqual([false]);
      mql.matches = true;
      handler?.();
      expect(seen).toEqual([false, true]);
      off();
      expect(mql.removeEventListener).toHaveBeenCalledWith('change', handler);
    });
    it('fallback addListener/removeListener (Safari < 14)', () => {
      let handler: (() => void) | undefined;
      const mql = {
        matches: true,
        addListener: vi.fn((h: () => void) => {
          handler = h;
        }),
        removeListener: vi.fn(),
      };
      const seen: boolean[] = [];
      const off = subscribeMediaQuery(mql, (m) => seen.push(m));
      mql.matches = false;
      handler?.();
      expect(seen).toEqual([true, false]);
      off();
      expect(mql.removeListener).toHaveBeenCalledWith(handler);
    });
  });
});
