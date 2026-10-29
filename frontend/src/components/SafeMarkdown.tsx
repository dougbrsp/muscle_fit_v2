'use client';

import type { ComponentProps } from 'react';
import ReactMarkdown, { type Components } from 'react-markdown';
import { sanitizeInternalPath } from '@/lib/safe-path';

/**
 * IA-15: renderizador ÚNICO de Markdown vindo de LLM (insights, análise de
 * treinos, recomendações de cardio). Todas as telas passam por aqui; cada uma
 * só escolhe o visual (`components`), nunca a política de segurança:
 *
 * - HTML cru nunca é interpretado: sem `rehype-raw`, o react-markdown mostra
 *   `<tag>` como texto escapado (comportamento padrão, mantido).
 * - Imagens viram texto (o `alt`): nenhuma `<img>` é criada, então o
 *   navegador não busca URL arbitrária ditada pela resposta da IA.
 * - Links: só `https:` ou caminho interno (`sanitizeInternalPath`, SEG-18);
 *   qualquer outro esquema (`javascript:`, `data:`, `http:`, `mailto:`...)
 *   vira texto simples. Link externo abre em nova aba com
 *   `rel="noopener noreferrer nofollow"`.
 */

export const SAFE_LINK_REL = 'noopener noreferrer nofollow';

/** `urlTransform` do react-markdown: devolve '' para tudo que não é seguro. */
export function safeMarkdownUrl(url: string): string {
  const internal = sanitizeInternalPath(url);
  if (internal) return internal;
  try {
    const parsed = new URL(url);
    if (parsed.protocol === 'https:') return parsed.href;
  } catch {
    // não é URL absoluta válida
  }
  return '';
}

function SafeLink({ href, children }: ComponentProps<'a'>) {
  if (!href) return <span>{children}</span>;
  const external = href.startsWith('https:');
  return (
    <a
      href={href}
      rel={SAFE_LINK_REL}
      {...(external ? { target: '_blank' } : {})}
    >
      {children}
    </a>
  );
}

function ImageAsText({ alt }: ComponentProps<'img'>) {
  return alt ? <span>{alt}</span> : null;
}

/** Elementos cuja política é fixa: sobrescrevem qualquer `components` da tela. */
const SAFE_ELEMENTS: Components = { a: SafeLink, img: ImageAsText };

export function SafeMarkdown({ children, components }: { children: string; components?: Components }) {
  return (
    <ReactMarkdown urlTransform={safeMarkdownUrl} components={{ ...components, ...SAFE_ELEMENTS }}>
      {children}
    </ReactMarkdown>
  );
}
