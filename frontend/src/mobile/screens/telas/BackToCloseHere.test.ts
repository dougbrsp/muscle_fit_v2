// @vitest-environment jsdom
import { createElement, act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { BackToCloseHere } from './BackToCloseHere';

// Entrada órfã no histórico: fechar a folha POR CÓDIGO (X, salvar, ler o código) desfaz a entrada que ela
// empurrou. Depois disso, o voltar seguinte age sobre a visão de baixo (o editor), não sobre um toque morto.

let root: Root | null = null;
let host: HTMLElement;

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});

afterEach(async () => {
  await act(async () => {
    root?.unmount();
  });
  root = null;
  host?.remove();
});

const esperar = async (vezes = 3) => {
  for (let i = 0; i < vezes; i++) await act(async () => { await new Promise((r) => setTimeout(r, 25)); });
};

describe('BackToCloseHere: abre e fecha por código sem deixar entrada órfã', () => {
  it('fechar a folha por código volta o histórico; o voltar seguinte fecha o editor', async () => {
    const fechouEditor = vi.fn();
    const fechouFolha = vi.fn();
    const push = vi.spyOn(window.history, 'pushState');
    const back = vi.spyOn(window.history, 'back');
    host = document.createElement('div');
    document.body.appendChild(host);
    root = createRoot(host);
    const render = (editor: boolean, folha: boolean) =>
      act(async () => {
        root!.render(
          createElement('div', null,
            createElement(BackToCloseHere, { open: editor, onClose: fechouEditor }),
            createElement(BackToCloseHere, { open: folha, onClose: fechouFolha }),
          ),
        );
      });

    await render(true, false);
    await render(true, true);
    expect(push).toHaveBeenCalledTimes(2);
    // Fecha a folha por código (props), sem gesto do usuário.
    await render(true, false);
    await esperar();
    expect(back).toHaveBeenCalledTimes(1);
    expect(fechouFolha).not.toHaveBeenCalled();
    // O popstate causado pelo próprio fechamento não fecha o editor.
    expect(fechouEditor).not.toHaveBeenCalled();
    // O voltar seguinte tem efeito sobre o editor, uma vez.
    await act(async () => { window.history.back(); });
    await esperar();
    expect(fechouEditor).toHaveBeenCalledTimes(1);
    expect(fechouFolha).not.toHaveBeenCalled();
  });

  it('fechar o editor por código também desfaz a entrada (comportamento igual ao do lançamento rápido)', async () => {
    const fechou = vi.fn();
    const back = vi.spyOn(window.history, 'back');
    back.mockClear();
    host = document.createElement('div');
    document.body.appendChild(host);
    root = createRoot(host);
    await act(async () => { root!.render(createElement(BackToCloseHere, { open: true, onClose: fechou })); });
    await act(async () => { root!.render(createElement(BackToCloseHere, { open: false, onClose: fechou })); });
    await esperar();
    expect(back).toHaveBeenCalledTimes(1);
    expect(fechou).not.toHaveBeenCalled();
  });
});
