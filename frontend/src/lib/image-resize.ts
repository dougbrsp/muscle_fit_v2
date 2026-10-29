// Redução de foto no navegador antes do upload (canvas), genérica.
//
// Por que existe (nutrição L6, foto do rótulo; plano §9 b):
//   - a foto do celular tem 3–12 MB e a API aceita bem menos (o limite é do
//     motor, `nutrition.label_photo.max_bytes` do JSON gerado);
//   - reencodar por canvas descarta EXIF/XMP: o corpo do upload passa a ter só a
//     imagem, sem o texto livre dos metadados que o WAF (CRS bloqueando XSS no
//     corpo desde o #579) poderia ler como ataque. Quem chama NUNCA envia o
//     arquivo cru como alternativa: se a redução falhar, o upload não acontece.
//
// Sem default numérico de propósito: borda, qualidade e tipo vêm de quem chama
// (na nutrição, do dono `domain/nutrition/label_photo` pelo JSON gerado; a guarda
// `foto_rotulo_numeros` varre este arquivo). A orientação do EXIF é aplicada na
// decodificação (`imageOrientation: 'from-image'`), antes de o EXIF ser perdido.

export type ResizeOptions = {
  /** Maior borda da imagem final, em px (a imagem nunca é ampliada). */
  maxEdge: number;
  /** Qualidade do encoder (0 a 1), como no `canvas.toBlob`. */
  quality: number;
  /** Tipo da imagem final (ex.: o JPEG que a API aceita). */
  contentType: string;
};

export type Dimensions = { width: number; height: number };

export class ImageResizeError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'ImageResizeError';
  }
}

/** Dimensões que cabem em `maxEdge` mantendo a proporção; nunca amplia. */
export function scaledDimensions(source: Dimensions, maxEdge: number): Dimensions {
  const { width, height } = source;
  if (!(width > 0) || !(height > 0) || !(maxEdge > 0)) {
    throw new ImageResizeError('Dimensões inválidas para reduzir a imagem.');
  }
  const scale = Math.min(1, maxEdge / Math.max(width, height));
  return {
    width: Math.max(1, Math.round(width * scale)),
    height: Math.max(1, Math.round(height * scale)),
  };
}

type Decoded = { source: CanvasImageSource; width: number; height: number; release: () => void };

async function decodeWithBitmap(file: Blob): Promise<Decoded | null> {
  if (typeof createImageBitmap !== 'function') return null;
  try {
    const bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' });
    return { source: bitmap, width: bitmap.width, height: bitmap.height, release: () => bitmap.close() };
  } catch {
    return null;
  }
}

function decodeWithImage(file: Blob): Promise<Decoded> {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () =>
      resolve({
        source: img,
        width: img.naturalWidth,
        height: img.naturalHeight,
        release: () => URL.revokeObjectURL(url),
      });
    img.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new ImageResizeError('Não foi possível abrir a imagem escolhida.'));
    };
    img.src = url;
  });
}

function encode(canvas: HTMLCanvasElement, options: ResizeOptions): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob(
      (blob) => {
        if (blob && blob.size > 0) resolve(blob);
        else reject(new ImageResizeError('Não foi possível reduzir a imagem neste navegador.'));
      },
      options.contentType,
      options.quality,
    );
  });
}

/**
 * Decodifica a foto (com a orientação do EXIF), desenha num canvas que cabe em
 * `maxEdge` e reencoda em `contentType` com `quality`. Sempre reencoda, mesmo
 * quando a foto já é pequena: é isso que tira os metadados do corpo do upload.
 * Lança `ImageResizeError` se o navegador não conseguir — nunca devolve o
 * arquivo original.
 */
export async function resizeImageForUpload(file: Blob, options: ResizeOptions): Promise<Blob> {
  const decoded = (await decodeWithBitmap(file)) ?? (await decodeWithImage(file));
  try {
    const { width, height } = scaledDimensions(decoded, options.maxEdge);
    const canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext('2d');
    if (!context) throw new ImageResizeError('Não foi possível reduzir a imagem neste navegador.');
    context.drawImage(decoded.source, 0, 0, width, height);
    return await encode(canvas, options);
  } finally {
    decoded.release();
  }
}
