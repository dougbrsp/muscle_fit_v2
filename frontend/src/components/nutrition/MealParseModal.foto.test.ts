// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement, act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { MEAL_PHOTO } from '@/lib/nutrition-constants';
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

async function abrir() {
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
          createElement(MealParseModal, { opened: true, onClose: vi.fn(), date: '2026-11-21', onParsed: vi.fn(), mode: 'photo' }),
        ),
      ),
    );
  });
}

async function digitar(el: HTMLInputElement | HTMLTextAreaElement, valor: string) {
  const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  await act(async () => {
    Object.getOwnPropertyDescriptor(proto, 'value')!.set!.call(el, valor);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

const campo = (rotulo: string) =>
  document.getElementById(
    (Array.from(document.querySelectorAll('label')).find((l) => l.textContent?.startsWith(rotulo)) as HTMLLabelElement).htmlFor,
  ) as HTMLInputElement & HTMLTextAreaElement;

async function enviar(arquivo: File) {
  const entrada = document.querySelector('input[type="file"]') as HTMLInputElement;
  Object.defineProperty(entrada, 'files', { value: [arquivo], configurable: true });
  await act(async () => {
    entrada.dispatchEvent(new Event('change', { bubbles: true }));
    await new Promise((r) => setTimeout(r, 50));
  });
}

describe('foto do prato: descrição e peso total opcionais', () => {
  it('sem preencher, o contexto é o de sempre e o limite da descrição é o do espelho', async () => {
    readMealPhoto.mockResolvedValue({ result: RESULTADO, photo: new Blob(['x']) });
    await abrir();
    expect(campo('Descreva a refeição').maxLength).toBe(MEAL_PHOTO.description_max_chars);
    await enviar(new File(['a'], 'a.jpg'));
    expect(readMealPhoto.mock.calls[0][1]).toEqual({ local_date: '2026-11-21', meal_slot: null });
  });

  it('preenchidos, descrição (sem espaços nas pontas) e peso vão no contexto', async () => {
    readMealPhoto.mockResolvedValue({ result: RESULTADO, photo: new Blob(['x']) });
    await abrir();
    await digitar(campo('Descreva a refeição'), '  arroz e frango ');
    await digitar(campo('Peso total'), '450');
    await enviar(new File(['b'], 'b.jpg'));
    expect(readMealPhoto.mock.calls[0][1]).toEqual({
      local_date: '2026-11-21',
      meal_slot: null,
      description: 'arroz e frango',
      total_grams: 450,
    });
  });

  it('peso acima do teto mostra erro e bloqueia o envio', async () => {
    await abrir();
    await digitar(campo('Peso total'), String(MEAL_PHOTO.total_grams_max + 1));
    expect(document.body.textContent).toContain(MEAL_PHOTO.total_grams_invalid_label);
    const tirar = Array.from(document.querySelectorAll('button')).find((b) => b.textContent?.includes('Tirar foto')) as HTMLButtonElement;
    expect(tirar.disabled).toBe(true);
  });

  it('peso abaixo do piso do espelho mostra a mensagem do espelho', async () => {
    await abrir();
    await digitar(campo('Peso total'), String(MEAL_PHOTO.total_grams_min - 1));
    expect(document.body.textContent).toContain(MEAL_PHOTO.total_grams_invalid_label);
  });
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
    // Depois do sucesso, nenhuma mutação guardada (cache/observador) segue referenciando o arquivo original.
    expect(client.getMutationCache().getAll().some((m) => m.state.variables === original)).toBe(false);
  });
});
