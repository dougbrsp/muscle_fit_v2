// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { act, createElement as h, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { ScreenErrorBoundary } from './ScreenErrorBoundary';

let root: Root | null = null;
let host: HTMLElement;
let failing = true;
let error: Error = new Error('boom');

function Child() {
  if (failing) throw error;
  return h('p', null, 'tela ok');
}

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  window.matchMedia ??= ((q: string) => ({
    matches: false,
    media: q,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    onchange: null,
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
});

afterEach(async () => {
  await act(async () => {
    root?.unmount();
  });
  root = null;
  host?.remove();
  failing = true;
  error = new Error('boom');
  vi.restoreAllMocks();
});

async function render(resetKey: string) {
  const ui: ReactNode = h(MantineProvider, null, h(ScreenErrorBoundary, { resetKey, children: h(Child) }));
  if (!root) {
    host = document.createElement('div');
    document.body.appendChild(host);
    root = createRoot(host);
  }
  await act(async () => {
    root!.render(ui);
  });
}

const retryButton = () => [...host.querySelectorAll('button')].find((b) => b.textContent?.includes('Tentar de novo'));

describe('ScreenErrorBoundary', () => {
  it('erro de render mostra a mensagem e o botão, sem relançar, e loga só o erro', async () => {
    const log = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    await render('/a');
    expect(host.textContent).toContain('Algo deu errado nesta tela.');
    expect(host.querySelector('[role="alert"]')).not.toBeNull();
    expect(retryButton()).toBeDefined();
    expect(log).toHaveBeenCalledWith(error);
  });

  it('"Tentar de novo" remonta a tela', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    await render('/a');
    failing = false;
    await act(async () => {
      retryButton()!.click();
    });
    expect(host.textContent).toContain('tela ok');
    expect(host.textContent).not.toContain('Algo deu errado');
  });

  it('mudar a chave de reinício (rota) limpa o erro', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    await render('/a');
    failing = false;
    await render('/b');
    expect(host.textContent).toContain('tela ok');
  });

  it('erro de chunk vira "Sem conexão" (tratamento de chunk), não a mensagem genérica', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    error = Object.assign(new Error('Loading chunk 12 failed'), { name: 'ChunkLoadError' });
    await render('/a');
    expect(host.textContent).toContain('Sem conexão');
    expect(host.textContent).not.toContain('Algo deu errado');
  });
});
