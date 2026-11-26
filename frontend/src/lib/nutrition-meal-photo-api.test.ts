import { beforeEach, describe, expect, it, vi } from 'vitest';

// Foto do prato (L10): a tela reduz por canvas com os parâmetros do dono, envia o JPEG
// reduzido em base64 sem padding num JSON e acompanha o job até o prazo que a API
// devolveu. O resultado abre o editor de sempre, com a origem que a API grava.
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
const { fetchMealPhotoUsage, readMealPhoto, uploadMealPhoto } = await import('./nutrition-api');
const { MEAL_PHOTO } = await import('./nutrition-constants');
const { editorFormFromPhoto } = await import('./nutrition-meal-parse');
const { mealCreatePayload } = await import('./nutrition-meal-form');

const JPEG_INICIO = new Uint8Array([0xff, 0xd8, 0xff, 0xe0]);
const CONTEXTO = { local_date: '2026-11-02', meal_slot: 'lunch', user_id: 12 };

beforeEach(() => {
  apiFetch.mockReset();
  resizeImageForUpload.mockReset();
  pollAsyncJob.mockReset();
});

describe('foto do prato: upload e leitura', () => {
  it('upload é JSON com a foto em base64 sem padding, o dia, a refeição e o aluno', async () => {
    apiFetch.mockResolvedValue({ job_id: 'j1', status: 'pending', poll_timeout_seconds: 240 });
    await uploadMealPhoto([new Blob([JPEG_INICIO])], CONTEXTO);
    const [url, opcoes] = apiFetch.mock.calls[0];
    expect(url).toBe('/nutrition/meals/photo');
    expect(opcoes.method).toBe('POST');
    expect(JSON.parse(opcoes.body)).toEqual({ ...CONTEXTO, images_base64: ['/9j/4A'] });
    expect(opcoes.body).not.toContain('=');
  });

  it('descrição e peso total vão no corpo só quando informados', async () => {
    apiFetch.mockResolvedValue({ job_id: 'j1', status: 'pending', poll_timeout_seconds: 240 });
    await uploadMealPhoto([new Blob([JPEG_INICIO])], { ...CONTEXTO, description: 'arroz e frango', total_grams: 450 });
    expect(JSON.parse(apiFetch.mock.calls[0][1].body)).toEqual({
      ...CONTEXTO,
      description: 'arroz e frango',
      total_grams: 450,
      images_base64: ['/9j/4A'],
    });
  });

  it('reduz com os parâmetros do dono, acompanha as etapas e usa o prazo da API', async () => {
    const reduzida = new Blob([JPEG_INICIO]);
    resizeImageForUpload.mockResolvedValue(reduzida);
    apiFetch.mockResolvedValue({ job_id: 'j1', status: 'pending', poll_timeout_seconds: 240 });
    pollAsyncJob.mockResolvedValue({ items: [], limitations: [] });
    const etapas: string[] = [];
    const original = new Blob(['cru']);

    const lido = await readMealPhoto([original], CONTEXTO, (etapa) => etapas.push(etapa));

    // Devolve a versão reduzida (a enviada), nunca o original; o corpo enviado é o de sempre.
    expect(lido.photos).toEqual([reduzida]);
    expect(lido.photos[0]).not.toBe(original);
    expect(lido.result).toEqual({ items: [], limitations: [] });
    expect(JSON.parse(apiFetch.mock.calls[0][1].body)).toEqual({ ...CONTEXTO, images_base64: ['/9j/4A'] });

    expect(resizeImageForUpload).toHaveBeenCalledWith(original, {
      maxEdge: MEAL_PHOTO.resize_max_edge_px,
      quality: MEAL_PHOTO.resize_quality,
      contentType: MEAL_PHOTO.content_type,
    });
    expect(etapas).toEqual(['resizing', 'uploading', 'reading']);
    expect(pollAsyncJob.mock.calls[0][0]).toBe('j1');
    expect(pollAsyncJob.mock.calls[0][1].timeoutMs).toBe(240_000);
  });

  it('se a redução falhar, nada é enviado', async () => {
    resizeImageForUpload.mockRejectedValue(new ImageResizeError('sem canvas'));
    await expect(readMealPhoto([new Blob(['x'])], CONTEXTO)).rejects.toThrow('sem canvas');
    expect(apiFetch).not.toHaveBeenCalled();
  });

  it('3 fotos: cada uma é reduzida e vão todas em images_base64, numa leitura só', async () => {
    resizeImageForUpload.mockImplementation(async () => new Blob([JPEG_INICIO]));
    apiFetch.mockResolvedValue({ job_id: 'j1', status: 'pending', poll_timeout_seconds: 240 });
    pollAsyncJob.mockResolvedValue({ items: [], limitations: [] });
    const lido = await readMealPhoto([new Blob(['1']), new Blob(['2']), new Blob(['3'])], CONTEXTO);
    expect(resizeImageForUpload).toHaveBeenCalledTimes(MEAL_PHOTO.images_per_reading);
    expect(apiFetch).toHaveBeenCalledTimes(1);
    expect(JSON.parse(apiFetch.mock.calls[0][1].body).images_base64).toEqual(['/9j/4A', '/9j/4A', '/9j/4A']);
    expect(lido.photos).toHaveLength(3);
  });

  it('soma das reduzidas acima do teto: não envia e mostra o rótulo do espelho', async () => {
    const grande = new Blob([new Uint8Array(MEAL_PHOTO.total_max_bytes / 2 + 1)]);
    resizeImageForUpload.mockResolvedValue(grande);
    await expect(readMealPhoto([new Blob(['1']), new Blob(['2'])], CONTEXTO)).rejects.toThrow(MEAL_PHOTO.total_too_large_label);
    expect(apiFetch).not.toHaveBeenCalled();
  });

  it('a cota do dia vem da API', async () => {
    apiFetch.mockResolvedValue({ feature: 'meal_photo', limit: 1, used: 0, remaining: 1, allowed: true });
    await fetchMealPhotoUsage();
    expect(apiFetch).toHaveBeenCalledWith('/nutrition/meals/photo/usage');
  });
});

describe('foto do prato: editor', () => {
  const RESULTADO = {
    items: [],
    limitations: [],
    notes: null,
    job_id: 'j1',
    user_id: 12,
    local_date: '2026-11-02',
    meal_slot: 'lunch',
    model: 'm',
    prompt_version: 'p',
    engine_version: 'e',
    plate_status: 'recognized',
  };

  it('grava com a origem do dono e sem texto descrito no formulário', () => {
    const form = editorFormFromPhoto(RESULTADO, { date: 'x', slot: null });
    expect(form.ai).toMatchObject({ job_id: 'j1', description_raw: '', ai_source: MEAL_PHOTO.meal_source });
    expect(form.local_date).toBe('2026-11-02');
    expect(mealCreatePayload(form)).toMatchObject({ ai_job_id: 'j1', ai_source: MEAL_PHOTO.meal_source });
  });

  it('a leitura por texto continua sem `ai_source`', async () => {
    const { editorFormFromParse } = await import('./nutrition-meal-parse');
    const form = editorFormFromParse(RESULTADO, { date: 'x', slot: null, text: 'arroz' });
    expect(mealCreatePayload(form)).not.toHaveProperty('ai_source');
  });
});
