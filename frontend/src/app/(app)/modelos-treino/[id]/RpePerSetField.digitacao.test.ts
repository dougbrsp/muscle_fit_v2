// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { createElement, act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { theme } from '@/theme';
import { RpePerSetField } from './RpePerSetField';

const WARNING = 'Valor fora da escala de RPE; mantido o anterior.';
let root: Root;
let host: HTMLElement;
const onCommit = vi.fn();

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  // O Mantine mede o DOM com estas APIs ausentes no jsdom.
  window.matchMedia ??= ((q: string) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {} })) as never;
});
beforeEach(async () => {
  onCommit.mockReset();
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root.render(
      createElement(MantineProvider, { theme }, createElement(RpePerSetField, { targets: [7, 8], setsCount: 2, onCommit })),
    );
  });
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

const field = (n: number) => host.querySelector<HTMLInputElement>(`input[aria-label="RPE esperado da série ${n}"]`)!;

function type(input: HTMLInputElement, text: string) {
  act(() => input.focus());
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, text);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
}
const leave = (input: HTMLInputElement) => act(() => input.blur());

describe('RpePerSetField — digitação (#237)', () => {
  it('"1" depois "15" e sair: restaura o último confirmado, não confirma 1 nem 10, e avisa', () => {
    const input = field(1);
    type(input, '1');
    type(input, '15');
    expect(input.value).toBe('15'); // o digitado não é trocado durante a edição
    leave(input);
    expect(onCommit).not.toHaveBeenCalled(); // nada mudou: segue [7, 8]
    expect(field(1).value).toBe('7');
    expect(host.textContent).toContain(WARNING);
  });

  it('o aviso some na próxima edição do campo', () => {
    type(field(1), '15');
    leave(field(1));
    expect(host.textContent).toContain(WARNING);
    type(field(1), '9');
    expect(host.textContent).not.toContain(WARNING);
  });

  it('valor válido confirma (8.5)', () => {
    type(field(1), '8.5');
    leave(field(1));
    expect(onCommit).toHaveBeenCalledWith([8.5, 8]);
    expect(host.textContent).not.toContain(WARNING);
  });

  it('vírgula decimal', () => {
    type(field(2), '9,5');
    leave(field(2));
    // Se o campo não aceitar vírgula, nunca confirma outro número: o valor anterior fica.
    const calls = onCommit.mock.calls;
    if (calls.length) expect(calls[0][0]).toEqual([7, 9.5]);
    else expect(field(2).value).toBe('8');
  });
});
