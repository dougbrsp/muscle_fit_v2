import { beforeEach, describe, expect, it, vi } from 'vitest';

// Foto do rótulo (L6): a tela reduz por canvas com os parâmetros do dono, envia
// o JPEG reduzido em base64 sem padding num JSON e acompanha o job até o prazo
// que a API devolveu. Se a redução falhar, nada é enviado (o arquivo cru nunca
// sai do aparelho).
const apiFetch = vi.fn();
const resizeImageForUpload = vi.fn();
const pollAsyncJob = vi.fn();

vi.mock('./api', async (importOriginal) => ({ ...(await importOriginal<typeof import('./api')>()), apiFetch }));
vi.mock('./image-resize', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./image-resize')>()),
  resizeImageForUpload,
}));
vi.mock('./async-job-poll', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./async-job-poll')>()),
  pollAsyncJob,
}));

const { ImageResizeError } = await import('./image-resize');
const { readLabelPhoto, uploadLabelPhoto } = await import('./nutrition-api');
const { LABEL_PHOTO } = await import('./nutrition-constants');

// 4 bytes -> base64 "/9j/4A==" (com padding); a tela manda sem os "=".
const JPEG_INICIO = new Uint8Array([0xff, 0xd8, 0xff, 0xe0]);

function corpo(chamada: unknown[]): { image_base64: string } {
  return JSON.parse((chamada[1] as RequestInit).body as string);
}

beforeEach(() => {
  apiFetch.mockReset();
  resizeImageForUpload.mockReset();
  pollAsyncJob.mockReset();
});

describe('foto do rótulo: upload e leitura', () => {
  it('upload é JSON com a foto em base64 sem padding (nenhum byte cru nem "=" no corpo)', async () => {
    apiFetch.mockResolvedValue({ job_id: 'j1', status: 'pending', poll_timeout_seconds: 240 });
    await uploadLabelPhoto(new Blob([JPEG_INICIO], { type: LABEL_PHOTO.content_type }));
    const [caminho, init] = apiFetch.mock.calls[0];
    expect(caminho).toBe('/nutrition/foods/label-photo');
    expect(init.method).toBe('POST');
    expect(Object.keys(corpo(apiFetch.mock.calls[0]))).toEqual(['image_base64']);
    expect(corpo(apiFetch.mock.calls[0]).image_base64).toBe('/9j/4A');
    expect(init.body).toMatch(/^\{"image_base64":"[A-Za-z0-9+/]*"\}$/);
  });

  it('reduz com os parâmetros do dono, envia o reduzido e espera o prazo da API', async () => {
    const original = new Blob(['foto-crua'], { type: 'image/heic' });
    const reduzida = new Blob([JPEG_INICIO], { type: LABEL_PHOTO.content_type });
    resizeImageForUpload.mockResolvedValue(reduzida);
    apiFetch.mockResolvedValue({ job_id: 'j2', status: 'pending', poll_timeout_seconds: 240 });
    pollAsyncJob.mockResolvedValue({ readable: true });
    const etapas: string[] = [];

    const leitura = await readLabelPhoto(original, (etapa) => etapas.push(etapa));

    expect(resizeImageForUpload).toHaveBeenCalledWith(original, {
      maxEdge: LABEL_PHOTO.resize_max_edge_px,
      quality: LABEL_PHOTO.resize_quality,
      contentType: LABEL_PHOTO.content_type,
    });
    expect(corpo(apiFetch.mock.calls[0]).image_base64).toBe('/9j/4A');
    expect(pollAsyncJob.mock.calls[0][0]).toBe('j2');
    expect(pollAsyncJob.mock.calls[0][1].timeoutMs).toBe(240_000);
    expect(etapas).toEqual(['resizing', 'uploading', 'reading']);
    expect(leitura).toEqual({ readable: true });
  });

  it('redução falhou: nada é enviado', async () => {
    resizeImageForUpload.mockRejectedValue(new ImageResizeError('sem canvas'));
    await expect(readLabelPhoto(new Blob(['x']))).rejects.toThrow('sem canvas');
    expect(apiFetch).not.toHaveBeenCalled();
    expect(pollAsyncJob).not.toHaveBeenCalled();
  });
});
