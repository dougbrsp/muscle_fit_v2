import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

import { KILL_SW_SOURCE, stampCacheVersion } from './build-sw.mjs';

const swSource = readFileSync(new URL('../public/sw.js', import.meta.url), 'utf8');

describe('build-sw', () => {
  it('carimba a versão e mantém o resto do sw.js intacto', () => {
    const out = stampCacheVersion(swSource, 'abc123');
    expect(out).toContain("const CACHE_VERSION = 'abc123';");
    expect(out.replace(/const CACHE_VERSION = '[^']*';/, '')).toBe(swSource.replace(/const CACHE_VERSION = '[^']*';/, ''));
  });

  it('rejeita versão inválida (sem injeção) e fonte sem a linha da versão', () => {
    expect(() => stampCacheVersion(swSource, "x'; evil()")).toThrow();
    expect(() => stampCacheVersion('// nada', 'v1')).toThrow();
  });

  it('o SW autodestrutivo apaga só musclefit-*, se desregistra e não tem fetch nem reload', () => {
    expect(KILL_SW_SOURCE).toContain("startsWith('musclefit-')");
    expect(KILL_SW_SOURCE).toContain('registration.unregister()');
    expect(KILL_SW_SOURCE).not.toMatch(/addEventListener\('fetch'/);
    expect(KILL_SW_SOURCE).not.toMatch(/navigate\(|reload\(/);
  });
});
