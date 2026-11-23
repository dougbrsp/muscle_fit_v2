// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { act, createElement as h, Component, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { ScreenErrorBoundary } from './ScreenErrorBoundary';

let root: Root | null = null;
let host: HTMLElement;
let error: Error = new Error('boom');

function Child(): never {
  throw error;
}

// Limite externo: prova que o erro de navegação do Next é relançado, não capturado.
class Outer extends Component<{ children: ReactNode }, { caught: unknown }> {
  state = { caught: null as unknown };
  static getDerivedStateFromError(caught: unknown) {
    return { caught };
  }
  render() {
    return this.state.caught ? h('p', null, 'capturado fora') : this.props.children;
  }
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
  error = new Error('boom');
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

async function render() {
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root!.render(
      h(MantineProvider, null, h(Outer, null, h(ScreenErrorBoundary, { resetKey: '/a', children: h(Child) }))),
    );
  });
}

const retryButton = () => [...host.querySelectorAll('button')].find((b) => b.textContent?.includes('Tentar de novo'));

describe('ScreenErrorBoundary: navegação e recarga', () => {
  it.each(['NEXT_REDIRECT;replace;/login;307;', 'NEXT_NOT_FOUND'])('relança erro de navegação do Next (%s)', async (digest) => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    error = Object.assign(new Error('nav'), { digest });
    await render();
    expect(host.textContent).toContain('capturado fora');
    expect(host.textContent).not.toContain('Algo deu errado');
  });

  it('erro de chunk: "Tentar de novo" recarrega a página', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const reload = vi.fn();
    vi.stubGlobal('location', { ...window.location, reload });
    error = Object.assign(new Error('Loading chunk 12 failed'), { name: 'ChunkLoadError' });
    await render();
    await act(async () => {
      retryButton()!.click();
    });
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it('erro comum: "Tentar de novo" não recarrega', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const reload = vi.fn();
    vi.stubGlobal('location', { ...window.location, reload });
    await render();
    await act(async () => {
      retryButton()!.click();
    });
    expect(reload).not.toHaveBeenCalled();
  });
});
