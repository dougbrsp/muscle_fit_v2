// @vitest-environment jsdom
import { MantineProvider } from '@mantine/core';
import { IconToolsKitchen2, IconUser } from '@tabler/icons-react';
import { act, createElement as h } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { MoreSheet } from './MoreSheet';
import { NUTRICAO_SECOES } from './nutricao-secoes';
import type { NavGroup } from './primary-nav';

const GROUPS: readonly NavGroup[] = [
  {
    title: 'Corpo e nutrição',
    items: [{ href: '/nutricao', label: 'Nutrição', icon: IconToolsKitchen2, children: NUTRICAO_SECOES }],
  },
  { title: 'Conta', items: [{ href: '/perfil', label: 'Perfil', icon: IconUser }] },
];

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
  await act(async () => root?.unmount());
  root = null;
  host?.remove();
});

async function montar(pathname: string, onClose = vi.fn()) {
  host = document.createElement('div');
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => {
    root!.render(
      h(
        MantineProvider,
        null,
        h(MoreSheet, { opened: true, onClose, groups: GROUPS, pathname, displayName: '', onLogout: vi.fn() }),
      ),
    );
  });
  const botao = document.querySelector<HTMLButtonElement>('button[aria-expanded]')!;
  const painel = document.getElementById(botao.getAttribute('aria-controls')!)!;
  return { botao, painel, onClose };
}

describe('MoreSheet: Nutrição expansível', () => {
  it('começa recolhido fora da Nutrição e expande ao tocar', async () => {
    const { botao, painel, onClose } = await montar('/perfil/');
    expect(botao.textContent).toContain('Nutrição');
    expect(botao.getAttribute('aria-expanded')).toBe('false');
    expect(painel.hidden).toBe(true);
    await act(async () => botao.click());
    expect(botao.getAttribute('aria-expanded')).toBe('true');
    expect(painel.hidden).toBe(false);
    const plano = painel.querySelector<HTMLAnchorElement>('a[href^="/nutricao/plano"]')!;
    expect(plano.textContent).toBe('Plano');
    plano.addEventListener('click', (e) => e.preventDefault()); // jsdom não navega
    await act(async () => plano.click());
    expect(onClose).toHaveBeenCalled();
    await act(async () => botao.click());
    expect(botao.getAttribute('aria-expanded')).toBe('false');
  });

  it('em /nutricao/plano abre expandido com Plano marcado (e só ele)', async () => {
    const { botao, painel } = await montar('/nutricao/plano/');
    expect(botao.getAttribute('aria-expanded')).toBe('true');
    const atuais = [...painel.querySelectorAll('[aria-current="page"]')].map((e) => e.textContent);
    expect(atuais).toEqual(['Plano']);
  });

  it('itens sem filhos continuam links diretos', async () => {
    await montar('/perfil/');
    const perfil = document.querySelector<HTMLAnchorElement>('a[href^="/perfil"]')!;
    expect(perfil.getAttribute('aria-current')).toBe('page');
    expect(document.querySelectorAll('button[aria-expanded]')).toHaveLength(1);
  });
});
