import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import {
  MOBILE_BREAKPOINT_PX,
  MOBILE_MEDIA_QUERY,
  layoutModeFromMatch,
  useLayoutMode,
} from './layout-mode';

describe('layout-mode', () => {
  it('breakpoint alinhado ao sm do Mantine (48em = 768px)', () => {
    expect(MOBILE_BREAKPOINT_PX).toBe(768);
    expect(MOBILE_MEDIA_QUERY).toBe('(max-width: 767.98px)');
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
});
