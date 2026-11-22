// @vitest-environment node
// Issue #250: currentTarget e nulo quando o atualizador funcional roda depois do handler; ler o valor antes.
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const SETTER_UPDATER = /set[A-Z]\w*\(\s*\(?\w*\)?\s*=>/g;

/** Linhas (1-based) com `.currentTarget` dentro de um atualizador funcional `setX((p) => ...)`. */
export function findCurrentTargetInUpdaters(source: string): number[] {
  const lines = new Set<number>();
  for (const m of source.matchAll(SETTER_UPDATER)) {
    const start = m.index! + m[0].indexOf('(');
    let depth = 0;
    let end = source.length;
    for (let i = start; i < source.length; i++) {
      if (source[i] === '(') depth++;
      else if (source[i] === ')' && --depth === 0) {
        end = i;
        break;
      }
    }
    for (const c of source.slice(start, end).matchAll(/\.currentTarget\b/g)) {
      lines.add(source.slice(0, start + c.index!).split('\n').length);
    }
  }
  return [...lines].sort((a, b) => a - b);
}

function listSources(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((d) => {
    const p = join(dir, d.name);
    if (d.isDirectory()) return d.name === 'node_modules' ? [] : listSources(p);
    return /\.tsx?$/.test(d.name) && !/\.test\.tsx?$/.test(d.name) ? [p] : [];
  });
}

describe('findCurrentTargetInUpdaters', () => {
  it('acusa uma linha', () => {
    expect(findCurrentTargetInUpdaters('setF((p) => ({ ...p, n: e.currentTarget.value }))')).toEqual([1]);
  });
  it('acusa varias linhas', () => {
    expect(findCurrentTargetInUpdaters('setF((p) => {\n  return {\n ...p, n: e.currentTarget.value };\n})')).toEqual([3]);
  });
  it('nao acusa valor lido antes, event.target nem set sem atualizador', () => {
    expect(findCurrentTargetInUpdaters('{ const v = e.currentTarget.value; setF((p) => ({ ...p, n: v })); }')).toEqual([]);
    expect(findCurrentTargetInUpdaters('setF((p) => ({ ...p, n: e.target.value }))')).toEqual([]);
    expect(findCurrentTargetInUpdaters('setX(e.currentTarget.value)')).toEqual([]);
  });
});

describe('guarda currentTarget em atualizadores', () => {
  it('nenhum arquivo de frontend/src le currentTarget dentro de atualizador funcional', () => {
    const found = listSources(join(process.cwd(), 'src')).flatMap((f) =>
      findCurrentTargetInUpdaters(readFileSync(f, 'utf8')).map((l) => `${f}:${l}`),
    );
    expect(found).toEqual([]);
  });
});
