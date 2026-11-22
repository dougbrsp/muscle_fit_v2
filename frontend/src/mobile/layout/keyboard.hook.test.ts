// @vitest-environment jsdom
import { createElement, act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { useKeyboardOpen } from './keyboard';

let root: Root;
let host: HTMLElement;
let open = false;
let input: HTMLInputElement;
let button: HTMLButtonElement;

function Probe() {
  open = useKeyboardOpen();
  return null;
}

const fire = (target: EventTarget, type: string) => act(() => void target.dispatchEvent(new Event(type, { bubbles: true })));
const advance = (ms: number) => act(() => void vi.advanceTimersByTime(ms));

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});
beforeEach(async () => {
  vi.useFakeTimers();
  host = document.createElement('div');
  input = document.createElement('input');
  input.type = 'text';
  button = document.createElement('button');
  document.body.append(host, input, button);
  root = createRoot(host);
  await act(async () => root.render(createElement(Probe)));
  act(() => input.focus());
});
afterEach(() => {
  act(() => root.unmount());
  document.body.innerHTML = '';
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('useKeyboardOpen — toque em curso (#252)', () => {
  it('foco em campo de texto abre', () => {
    expect(open).toBe(true);
  });

  it('pointerdown + focusout não fecha enquanto pressiona; ao soltar, fecha depois do click', () => {
    fire(button, 'pointerdown');
    act(() => input.blur()); // o campo perde o foco no toque (iOS: botão não recebe foco)
    advance(100);
    expect(open).toBe(true);
    fire(button, 'pointerup');
    expect(open).toBe(true); // ainda não: o click vem antes
    advance(10);
    expect(open).toBe(false);
  });

  it('sequência real do navegador: pointerdown, focusout, focusin no botão, 150 ms, mutação; fecha só depois do click', () => {
    fire(button, 'pointerdown');
    act(() => button.focus()); // Chromium: o campo perde o foco e o botão recebe (focusout + focusin)
    document.body.append(document.createElement('span')); // mutação de árvore durante o toque
    advance(0); // deixa o MutationObserver rodar
    advance(150);
    expect(open).toBe(true);
    fire(button, 'pointerup');
    fire(button, 'click');
    expect(open).toBe(true);
    advance(10);
    expect(open).toBe(false);
  });

  it('focusout sem toque (Tab) fecha depois do atraso', () => {
    act(() => input.blur());
    expect(open).toBe(true);
    advance(100);
    expect(open).toBe(false);
  });

  it('focusout (Tab) agendado e pointerdown antes dos 60 ms: o sync não roda durante o toque', () => {
    act(() => input.blur());
    fire(button, 'pointerdown');
    advance(100);
    expect(open).toBe(true);
    fire(button, 'pointerup');
    advance(10);
    expect(open).toBe(false);
  });

  it('pointercancel não prende o estado', () => {
    fire(button, 'pointerdown');
    act(() => input.blur());
    fire(button, 'pointercancel');
    advance(10);
    expect(open).toBe(false);
  });

  it('blur da janela no meio do toque não prende o estado', () => {
    fire(button, 'pointerdown');
    act(() => input.blur());
    act(() => void window.dispatchEvent(new Event('blur')));
    advance(10);
    expect(open).toBe(false);
    act(() => input.focus());
    act(() => input.blur());
    advance(100);
    expect(open).toBe(false); // sem "pressionando" preso: focusout volta a fechar
  });

  it('toque que passa de um campo de texto para outro continua aberto', () => {
    const other = document.createElement('input');
    document.body.append(other);
    fire(other, 'pointerdown');
    act(() => other.focus());
    fire(other, 'pointerup');
    advance(100);
    expect(open).toBe(true);
  });

  it('desmontar remove os listeners', () => {
    const spy = vi.spyOn(document, 'removeEventListener');
    const winSpy = vi.spyOn(window, 'removeEventListener');
    act(() => root.unmount());
    const types = spy.mock.calls.map((c) => c[0]);
    for (const t of ['pointerdown', 'pointerup', 'pointercancel', 'focusin', 'focusout', 'visibilitychange']) {
      expect(types).toContain(t);
    }
    expect(winSpy.mock.calls.map((c) => c[0])).toContain('blur');
    root = createRoot(host); // o afterEach desmonta de novo
  });
});
