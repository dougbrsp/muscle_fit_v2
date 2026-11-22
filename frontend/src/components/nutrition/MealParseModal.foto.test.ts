// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement, act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import type { MealPhotoResult } from '@/lib/nutrition-types';
import { MealParseModal } from './MealParseModal';

// Issue #233: o rascunho da refeição guarda a foto REDUZIDA (a que o servidor recebeu), não o original.
const readMealPhoto = vi.hoisted(() => vi.fn());
vi.mock('@/lib/nutrition-api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/nutrition-api')>()),
  readMealPhoto,
  fetchMealPhotoUsage: vi.fn().mockResolvedValue({ feature: 'meal_photo', limit: 5, used: 0, remaining: 5, allowed: true }),
}));

const RESULTADO = {
  items: [
    {
      index: 0,
      ai: { name: 'arroz', quantity: null, unit: 'g', grams_estimate: 100, preparation: null, confidence: 0.9 },
      status: 'selected',
      candidates: [],
      food_id: null,
      match_confidence: null,
      quantity: 100,
      unit: 'g',
      estimate: null,
      candidate_for_catalog: false,
      uses_estimate: false,
      limitations: [],
    },
  ],
  limitations: [],
  notes: null,
  job_id: 'j1',
  user_id: 1,
  local_date: '2026-11-21',
  meal_slot: 'lunch',
} as unknown as MealPhotoResult;

let root: Root | null = null;
let host: HTMLElement;

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  window.matchMedia ||= ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener() {},
    removeListener() {},
    addEventListener() {},
    removeEventListener() {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
  (globalThis as { ResizeObserver?: unknown }).ResizeObserver ||= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
});

afterEach(async () => {
  await act(async () => {
    root?.unmount();
  });
  root = null;
  host?.remove();
  document.body.innerHTML = '';
  vi.clearAllMocks();
});

describe('foto do prato: o modal entrega a versão reduzida', () => {
  it('onParsed recebe o formulário com a foto reduzida, nunca o arquivo original', async () => {
    const original = new File(['cru-12MB'], 'prato.jpg', { type: 'image/jpeg' });
    const reduzida = new Blob(['jpeg-reduzido'], { type: 'image/jpeg' });
    readMealPhoto.mockResolvedValue({ result: RESULTADO, photo: reduzida });
    const onParsed = vi.fn();

    host = document.createElement('div');
    document.body.appendChild(host);
    root = createRoot(host);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await act(async () => {
      root!.render(
        createElement(
          QueryClientProvider,
          { client },
          createElement(
            MantineProvider,
            { env: 'test' },
            createElement(MealParseModal, { opened: true, onClose: vi.fn(), date: '2026-11-21', onParsed, mode: 'photo' }),
          ),
        ),
      );
    });

    const entrada = document.querySelector('input[type="file"]') as HTMLInputElement;
    Object.defineProperty(entrada, 'files', { value: [original], configurable: true });
    await act(async () => {
      entrada.dispatchEvent(new Event('change', { bubbles: true }));
      await new Promise((r) => setTimeout(r, 50));
    });

    expect(readMealPhoto.mock.calls[0][0]).toBe(original);
    expect(onParsed).toHaveBeenCalledTimes(1);
    const form = onParsed.mock.calls[0][0];
    expect(form.photo).toBe(reduzida);
    expect(form.photo).not.toBe(original);
  });
});
