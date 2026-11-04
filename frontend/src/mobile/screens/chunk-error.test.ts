import { describe, expect, it } from 'vitest';
import { isChunkLoadError } from './chunk-error';

describe('isChunkLoadError', () => {
  it('reconhece falha de chunk e de import dinamico', () => {
    const named = new Error('x');
    named.name = 'ChunkLoadError';
    expect(isChunkLoadError(named)).toBe(true);
    expect(isChunkLoadError(new Error('Loading chunk 123 failed.'))).toBe(true);
    expect(isChunkLoadError(new Error('Loading CSS chunk 9 failed'))).toBe(true);
    expect(isChunkLoadError(new TypeError('Failed to fetch dynamically imported module: /a.js'))).toBe(true);
    expect(isChunkLoadError(new TypeError('Importing a module script failed.'))).toBe(true);
  });
  it('erros de render comuns NAO sao falha de chunk', () => {
    expect(isChunkLoadError(new Error('Cannot read properties of undefined'))).toBe(false);
    expect(isChunkLoadError(new TypeError('x is not a function'))).toBe(false);
    expect(isChunkLoadError(null)).toBe(false);
    expect(isChunkLoadError('Loading chunk')).toBe(false);
  });
});
