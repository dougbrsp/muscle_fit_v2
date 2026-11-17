// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createElement, act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { FOOD_PER_100_FIELDS, FOOD_SERVING_FIELDS } from '@/lib/nutrition-constants';
import { FoodForm } from './FoodForm';

// #141: campos numéricos pedem teclado decimal; vírgula e ponto valem como separador; letra, sinal e "1e3" não entram.

const criar = vi.fn(async (payload: unknown) => ({ id: 1, name: 'x', warnings: [], payload }));
vi.mock('@/lib/nutrition-api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/nutrition-api')>()),
  createFood: (payload: unknown) => criar(payload),
}));

let root: Root | null = null;
let host: HTMLElement;

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  window.matchMedia ||= ((query: string) => ({
    matches: false, media: query, onchange: null, addListener() {}, removeListener() {},
    addEventListener() {}, removeEventListener() {}, dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
  class RO { observe() {} unobserve() {} disconnect() {} }
  (globalThis as { ResizeObserver?: unknown }).ResizeObserver ||= RO;
});

beforeEach(async () => {
  criar.mockClear();
  host = document.createElement('div');
  document.body.appendChild(host);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  root = createRoot(host);
  await act(async () => {
    root!.render(
      createElement(
        QueryClientProvider,
        { client },
        createElement(MantineProvider, { env: 'test' }, createElement(FoodForm, { food: null, onSaved: vi.fn(), onCancel: vi.fn() })),
      ),
    );
  });
});

afterEach(async () => {
  await act(async () => { root?.unmount(); });
  root = null;
  host.remove();
  document.body.innerHTML = '';
});

const esperar = () => act(async () => { await new Promise((r) => setTimeout(r, 25)); });
const rotulo = (code: string) => FOOD_PER_100_FIELDS.find((c) => c.code === code)!.label;
const rotuloPorcao = (code: string) => FOOD_SERVING_FIELDS.find((c) => c.code === code)!.label;
const campo = (inicio: string) => {
  const label = Array.from(host.querySelectorAll('label')).find((l) => l.textContent?.startsWith(inicio));
  return document.getElementById(label!.htmlFor) as HTMLInputElement;
};
async function digitar(el: HTMLInputElement, valor: string) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(el, valor);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
  await esperar();
}
/** Tecla a tecla, como no teclado: o ponto digitado vira o separador decimal. */
async function teclar(el: HTMLInputElement, texto: string) {
  for (let i = 1; i <= texto.length; i++) await digitar(el, el.value + texto[i - 1]);
}
async function cadastrar(): Promise<Record<string, unknown>> {
  await digitar(campo('Nome'), 'Teste');
  const botao = Array.from(host.querySelectorAll('button')).find((b) => b.textContent?.includes('Cadastrar alimento'))!;
  await act(async () => { botao.click(); });
  await esperar();
  return criar.mock.calls[0]?.[0] as Record<string, unknown>;
}

const CODIGOS = ['energy_kcal', 'carbohydrates_g', 'protein_g', 'total_fat_g', 'fiber_g'];

describe('FoodForm: teclado decimal', () => {
  it('kcal, carboidratos, proteínas, gorduras, fibras, porção e embalagem pedem inputMode decimal; o EAN, numeric', () => {
    for (const code of CODIGOS) expect(campo(rotulo(code)).getAttribute('inputmode'), code).toBe('decimal');
    expect(campo('Valores da tabela por').getAttribute('inputmode')).toBe('decimal');
    expect(campo(rotuloPorcao('serving_size')).getAttribute('inputmode')).toBe('decimal');
    expect(campo(rotuloPorcao('servings_per_package')).getAttribute('inputmode')).toBe('decimal');
    expect(campo('Código de barras').getAttribute('inputmode')).toBe('numeric');
  });

  it.each([['3,5'], ['3.5']])('"%s" vira 3,5 (nunca 35)', async (texto) => {
    await teclar(campo(rotulo('protein_g')), texto);
    expect((await cadastrar()).protein_g).toBe(3.5);
  });

  it('2 casas mantidas, sem arredondar além', async () => {
    await digitar(campo(rotulo('protein_g')), '3,456');
    expect((await cadastrar()).protein_g).toBe(3.45);
  });

  async function colar(el: HTMLInputElement, texto: string) {
    const ev = new Event('paste', { bubbles: true, cancelable: true });
    (ev as unknown as { clipboardData: unknown }).clipboardData = { getData: () => texto };
    await act(async () => { el.dispatchEvent(ev); });
    await esperar();
  }

  it.each([['energy_kcal'], ['protein_g']])('colar "3.5" e "3,5" em %s persiste 3.5', async (code) => {
    for (const texto of ['3.5', ' 3,5 ']) {
      const el = campo(rotulo(code));
      await colar(el, texto);
      expect(el.value).toBe('3,5');
    }
    expect((await cadastrar())[code]).toBe(3.5);
  });

  it('colar "3,456" grava no máximo 2 casas', async () => {
    await colar(campo(rotulo('protein_g')), '3,456');
    expect((await cadastrar()).protein_g).toBe(3.45);
  });

  it.each([['1e3'], ['-5'], ['abc'], ['1.234,5'], ['']])('colar "%s" não altera o campo', async (texto) => {
    for (const code of ['energy_kcal', 'protein_g']) {
      const el = campo(rotulo(code));
      await digitar(el, '7');
      await colar(el, texto);
      expect(el.value).toBe('7');
    }
    const dados = await cadastrar();
    expect([dados.energy_kcal, dados.protein_g]).toEqual([7, 7]);
  });

  // O Mantine descarta o caractere inválido (letra, "-", "e") e fica com os dígitos: nunca 1000 nem -5.
  it.each([['abc', '', null], ['-5', '5', 5], ['1e3', '13', 13]])('"%s" fica "%s" e persiste %s', async (texto, exibido, persistido) => {
    const el = campo(rotulo('protein_g'));
    await digitar(el, texto);
    expect(el.value).toBe(exibido);
    expect((await cadastrar()).protein_g).toBe(persistido);
  });
});
