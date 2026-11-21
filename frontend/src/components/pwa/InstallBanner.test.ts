// @vitest-environment jsdom
import { createElement, act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('next/navigation', () => ({ usePathname: () => '/checkin' }));
vi.mock('@/lib/mobile/flags', () => ({ useMobileUi: () => true }));
vi.mock('@/lib/mobile/layout-mode', () => ({ useLayoutMode: () => 'mobile' }));
vi.mock('@/lib/pwa/use-install-prompt', () => ({
  useInstallPrompt: () => ({ mode: 'ios-safari', dismissed: false, promptInstall: vi.fn(), dismiss: vi.fn() }),
}));
// Mantine precisa de provider/matchMedia; aqui só importa o que a dica renderiza.
vi.mock('@mantine/core', async () => {
  const { createElement: h } = await import('react');
  const box = (tag: string) => ({ children, ...p }: Record<string, unknown>) =>
    h(tag, { 'data-testid': p['data-testid'] as string | undefined }, children as never);
  return { Paper: box('section'), Group: box('div'), Text: box('p'), Button: box('button'), ActionIcon: box('button') };
});
vi.mock('@tabler/icons-react', () => ({ IconX: () => null }));

import { InstallBanner } from './InstallBanner';

let root: Root;
let host: HTMLElement;
let input: HTMLInputElement;
const banner = () => document.querySelector('[data-testid="pwa-install-banner"]');
const advance = (ms: number) => act(() => void vi.advanceTimersByTime(ms));

beforeAll(() => {
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
});
beforeEach(async () => {
  vi.useFakeTimers();
  host = document.createElement('div');
  input = document.createElement('input');
  input.type = 'text';
  document.body.append(host, input);
  root = createRoot(host);
  await act(async () => root.render(createElement(InstallBanner)));
});
afterEach(() => {
  act(() => root.unmount());
  document.body.innerHTML = '';
  vi.useRealTimers();
});

describe('InstallBanner com o teclado aberto (#252)', () => {
  it('aparece sem foco, some com campo de texto em foco e volta ao sair do campo', () => {
    expect(banner()).not.toBeNull();
    act(() => input.focus());
    expect(banner()).toBeNull();
    act(() => input.blur());
    advance(100);
    expect(banner()).not.toBeNull();
  });
});
