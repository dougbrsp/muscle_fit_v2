import { describe, expect, it } from 'vitest';
import { safeInternalHref } from './safe-href';

describe('safeInternalHref', () => {
  it('aceita caminhos internos', () => {
    expect(safeInternalHref('/dashboard/recovery')).toBe('/dashboard/recovery');
    expect(safeInternalHref('/insights?x=1')).toBe('/insights?x=1');
  });

  it('rejeita externos, protocol-relative e esquemas perigosos', () => {
    const ruins = ['https://evil.test', '//evil.test', '/\\evil.test', 'javascript:alert(1)', 'dashboard', '', '/a\nb'];
    for (const bad of ruins) {
      expect(safeInternalHref(bad), bad).toBeNull();
    }
    expect(safeInternalHref(null)).toBeNull();
    expect(safeInternalHref(undefined)).toBeNull();
  });
});
