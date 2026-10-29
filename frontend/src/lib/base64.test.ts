import { describe, expect, it } from 'vitest';

import { blobToBase64 } from './base64';

describe('blobToBase64', () => {
  it('com e sem padding', async () => {
    const blob = new Blob([new Uint8Array([0xff, 0xd8, 0xff, 0xe0])]);
    expect(await blobToBase64(blob, { padding: true })).toBe('/9j/4A==');
    expect(await blobToBase64(blob, { padding: false })).toBe('/9j/4A');
  });

  it('blob grande (várias fatias) dá o mesmo base64 do Node', async () => {
    const bytes = new Uint8Array(200_000).map((_, i) => (i * 37) % 256);
    const esperado = Buffer.from(bytes).toString('base64');
    expect(await blobToBase64(new Blob([bytes]), { padding: true })).toBe(esperado);
    expect(await blobToBase64(new Blob([bytes]), { padding: false })).toBe(esperado.replace(/=+$/, ''));
  });

  it('blob vazio vira texto vazio', async () => {
    expect(await blobToBase64(new Blob([]), { padding: false })).toBe('');
  });
});
