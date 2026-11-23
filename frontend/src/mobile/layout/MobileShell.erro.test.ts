// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { act, createElement as h } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';

const nav = vi.hoisted(() => ({ pathname: '/treinos/' }));

vi.mock('next/navigation', () => ({
  usePathname: () => nav.pathname,
  useRouter: () => ({ back: vi.fn(), replace: vi.fn(), push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(''),
}));
vi.mock('@tanstack/react-query', () => ({ useQueryClient: () => ({}) }));
vi.mock('../use-current-user', () => ({ useCurrentUser: () => ({ data: undefined }) }));
vi.mock('../screens/registry', () => ({ resolveMobileScreen: () => null }));
vi.mock('../screens/DesktopOnlyScreen', () => ({ DesktopOnlyScreen: () => h('p', null, 'so no desktop') }));
vi.mock('../ui', async (orig) => ({ ...(await orig<object>()), OfflineBanner: () => null }));
vi.mock('./MoreSheet', () => ({ MoreSheet: () => null }));
vi.mock('./keyboard', () => ({ useKeyboardOpen: () => false }));
vi.mock('@/lib/app-logout', () => ({ logoutAndRedirect: vi.fn() }));

import MobileShell from './MobileShell';

let root: Root | null = null;
let host: HTMLElement;

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
  vi.restoreAllMocks();
});

function Quebra(): never {
  throw new Error('boom');
}

describe('MobileShell: erro no conteúdo da página', () => {
  it('filho (children) que lança é contido pelo limite; cabeçalho e navegação seguem na tela', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    host = document.createElement('div');
    document.body.appendChild(host);
    root = createRoot(host);
    await act(async () => {
      root!.render(h(MantineProvider, null, h(MobileShell, null, h(Quebra))));
    });
    expect(host.textContent).toContain('Algo deu errado nesta tela.');
    expect(host.querySelector('nav[aria-label="Navegação principal"]')).not.toBeNull();
    expect(host.querySelector('header')).not.toBeNull();
  });
});
