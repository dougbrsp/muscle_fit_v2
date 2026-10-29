import { afterEach, describe, expect, it, vi } from 'vitest';

import { ImageResizeError, resizeImageForUpload, scaledDimensions } from './image-resize';

// Ambiente "node" (sem DOM): `createImageBitmap` e o canvas são dublês que
// registram o que o helper pediu. Os parâmetros vêm de quem chama (sem default).
const OPCOES = { maxEdge: 640, quality: 0.5, contentType: 'image/jpeg' };

type CanvasFalso = {
  width: number;
  height: number;
  desenhos: unknown[][];
  pedidos: unknown[][];
  getContext: (tipo: string) => { drawImage: (...args: unknown[]) => void } | null;
  toBlob: (cb: (blob: Blob | null) => void, tipo: string, qualidade: number) => void;
};

function instalar(opcoes: { largura: number; altura: number; blob?: Blob | null; semContexto?: boolean }) {
  const fechar = vi.fn();
  const canvas: CanvasFalso = {
    width: 0,
    height: 0,
    desenhos: [],
    pedidos: [],
    getContext: () => (opcoes.semContexto ? null : { drawImage: (...args: unknown[]) => canvas.desenhos.push(args) }),
    toBlob: (cb, tipo, qualidade) => {
      canvas.pedidos.push([tipo, qualidade]);
      cb(opcoes.blob === undefined ? new Blob(['jpeg-reencodado'], { type: tipo }) : opcoes.blob);
    },
  };
  const bitmap = { width: opcoes.largura, height: opcoes.altura, close: fechar };
  const decodificar = vi.fn(async () => bitmap);
  vi.stubGlobal('createImageBitmap', decodificar);
  vi.stubGlobal('document', { createElement: (tag: string) => (tag === 'canvas' ? canvas : null) });
  return { canvas, decodificar, fechar, bitmap };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('scaledDimensions', () => {
  it('reduz pela maior borda mantendo a proporção e nunca amplia', () => {
    expect(scaledDimensions({ width: 4000, height: 3000 }, 1000)).toEqual({ width: 1000, height: 750 });
    expect(scaledDimensions({ width: 3000, height: 4000 }, 1000)).toEqual({ width: 750, height: 1000 });
    expect(scaledDimensions({ width: 800, height: 600 }, 1000)).toEqual({ width: 800, height: 600 });
  });

  it('dimensão inválida é erro, não imagem vazia', () => {
    expect(() => scaledDimensions({ width: 0, height: 10 }, 100)).toThrow(ImageResizeError);
    expect(() => scaledDimensions({ width: 10, height: 10 }, 0)).toThrow(ImageResizeError);
  });
});

describe('resizeImageForUpload', () => {
  it('decodifica com a orientação do EXIF, desenha na borda pedida e reencoda no tipo e qualidade de quem chama', async () => {
    const { canvas, decodificar, fechar, bitmap } = instalar({ largura: 4032, altura: 3024 });
    const original = new Blob(['foto-crua-com-exif'], { type: 'image/heic' });

    const saida = await resizeImageForUpload(original, OPCOES);

    expect(decodificar).toHaveBeenCalledWith(original, { imageOrientation: 'from-image' });
    expect([canvas.width, canvas.height]).toEqual([640, 480]);
    expect(canvas.desenhos).toEqual([[bitmap, 0, 0, 640, 480]]);
    expect(canvas.pedidos).toEqual([['image/jpeg', 0.5]]);
    expect(saida).not.toBe(original);
    expect(saida.type).toBe('image/jpeg');
    expect(fechar).toHaveBeenCalledOnce();
  });

  it('foto já pequena também é reencodada (tira os metadados do corpo do upload)', async () => {
    const { canvas } = instalar({ largura: 300, altura: 200 });
    const original = new Blob(['jpeg-pequeno'], { type: 'image/jpeg' });
    const saida = await resizeImageForUpload(original, OPCOES);
    expect([canvas.width, canvas.height]).toEqual([300, 200]);
    expect(canvas.pedidos).toHaveLength(1);
    expect(saida).not.toBe(original);
  });

  it('falha do encoder ou sem contexto 2D é erro: nunca devolve o arquivo cru', async () => {
    const original = new Blob(['x'], { type: 'image/jpeg' });
    const semBlob = instalar({ largura: 100, altura: 100, blob: null });
    await expect(resizeImageForUpload(original, OPCOES)).rejects.toThrow(ImageResizeError);
    expect(semBlob.fechar).toHaveBeenCalledOnce();
    vi.unstubAllGlobals();
    instalar({ largura: 100, altura: 100, semContexto: true });
    await expect(resizeImageForUpload(original, OPCOES)).rejects.toThrow(ImageResizeError);
  });
});
