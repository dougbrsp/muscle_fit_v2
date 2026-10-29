import { MantineProvider } from '@mantine/core';
import { createElement, type ComponentProps, type ReactElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ReactMarkdown from 'react-markdown';
import { describe, expect, it } from 'vitest';
import { AiMarkdown } from '@/components/dashboard/AiMarkdown';
import { theme } from '@/theme';
import { SAFE_LINK_REL, SafeMarkdown, safeMarkdownUrl } from './SafeMarkdown';

/** IA-15: Markdown vindo de LLM sem imagem externa, link perigoso nem HTML cru. */

function html(element: ReactElement): string {
  return renderToStaticMarkup(createElement(MantineProvider, { theme }, element));
}

const safe = (md: string) => html(createElement(SafeMarkdown, null, md));
const ai = (md: string) => html(createElement(AiMarkdown, null, md));

describe('safeMarkdownUrl', () => {
  it.each([
    ['https://exemplo.com/a?b=1', 'https://exemplo.com/a?b=1'],
    ['/modelos-treino/1', '/modelos-treino/1'],
  ])('mantém %s', (url, esperado) => {
    expect(safeMarkdownUrl(url)).toBe(esperado);
  });

  it.each([
    'javascript:alert(1)',
    'JAVASCRIPT:alert(1)',
    'data:text/html,<script>alert(1)</script>',
    'vbscript:msgbox',
    'http://exemplo.com',
    'mailto:a@b.c',
    '//evil.com',
    '/\\evil.com',
    '/%2f%2fevil.com',
    'relativo/sem/barra',
    '#ancora',
  ])('descarta %s', (url) => {
    expect(safeMarkdownUrl(url)).toBe('');
  });
});

describe('SafeMarkdown', () => {
  it('não cria <img> para imagem externa: mostra só o alt', () => {
    const out = safe('Veja ![grafico de carga](https://evil.example/pixel.png?u=1)');
    expect(out).not.toContain('<img');
    expect(out).not.toContain('evil.example');
    expect(out).toContain('grafico de carga');
  });

  it('imagem por referência também vira texto', () => {
    const out = safe('![x][ref]\n\n[ref]: https://evil.example/a.png');
    expect(out).not.toContain('<img');
    expect(out).not.toContain('evil.example');
  });

  it('link https recebe rel noopener noreferrer nofollow e nova aba', () => {
    const out = safe('[artigo](https://exemplo.com/estudo)');
    expect(out).toContain('href="https://exemplo.com/estudo"');
    expect(out).toContain(`rel="${SAFE_LINK_REL}"`);
    expect(out).toContain('target="_blank"');
  });

  it('link interno mantém o caminho, sem nova aba', () => {
    const out = safe('[modelo](/modelos-treino/1)');
    expect(out).toContain('href="/modelos-treino/1"');
    expect(out).toContain(`rel="${SAFE_LINK_REL}"`);
    expect(out).not.toContain('target=');
  });

  it.each([
    '[clique](javascript:alert(1))',
    '[clique](data:text/html;base64,PHNjcmlwdD4=)',
    '[clique](http://exemplo.com)',
    '[clique](//evil.com)',
    '<javascript:alert(1)>',
  ])('link perigoso %s vira texto, sem <a>', (md) => {
    const out = safe(md);
    expect(out).not.toContain('<a');
    expect(out).not.toMatch(/href=/);
    expect(out.toLowerCase()).not.toContain('href="javascript');
  });

  it('HTML cru não é interpretado: aparece escapado como texto', () => {
    const out = safe('Oi <script>alert(1)</script> <img src="https://evil.example/x.png" onerror="alert(1)"> <b>negrito</b>');
    expect(out).not.toContain('<script');
    expect(out).not.toContain('<img');
    expect(out).not.toContain('<b>');
    expect(out).toContain('&lt;script&gt;');
  });

  it('a política vence o components da tela', () => {
    // `children` vai como 3º argumento (regra react/no-children-prop); o cast
    // só satisfaz o tipo de props, que exige `children`.
    const props = {
      components: { img: () => createElement('img', { src: 'https://evil.example/x.png' }) },
    } as unknown as ComponentProps<typeof SafeMarkdown>;
    const out = html(createElement(SafeMarkdown, props, '![a](https://evil.example/x.png)'));
    expect(out).not.toContain('<img');
  });
});

describe('sem mudança visível no Markdown atual das recomendações', () => {
  // Formato real devolvido pela IA (títulos, parágrafos, negrito e listas, sem links).
  const RECOMENDACAO = [
    '## Resumo da semana',
    '',
    'Você treinou **4 vezes** com boa regularidade.',
    '',
    '### Pontos de atenção',
    '',
    '- Volume de costas abaixo da faixa',
    '- RPE alto nas últimas séries',
    '',
    '1. Mantenha o descanso',
    '2. Ajuste a carga',
  ].join('\n');

  it('SafeMarkdown sem components renderiza igual ao ReactMarkdown puro', () => {
    expect(safe(RECOMENDACAO)).toBe(html(createElement(ReactMarkdown, null, RECOMENDACAO)));
  });

  it('AiMarkdown mantém a mesma árvore de antes (componentes Mantine)', () => {
    const out = ai(RECOMENDACAO);
    expect(out).toContain('Resumo da semana');
    expect(out).toContain('<strong');
    expect(out).toContain('<ul');
    expect(out).toContain('<ol');
    expect(out).not.toContain('<a');
    expect(out).not.toContain('<img');
  });
});
