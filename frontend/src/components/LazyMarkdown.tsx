'use client';

import dynamic from 'next/dynamic';

/**
 * COD-14: o Markdown da IA (`react-markdown` + micromark, ~35 kB gzip) carregado
 * sob demanda. Mesmo componente, mesma política de segurança (IA-15): isto só
 * muda QUANDO o código chega, não o que é desenhado.
 *
 * Sem placeholder: o texto da IA só existe depois que a consulta ou a geração
 * termina, e `preloadMarkdown()` (chamado na montagem da tela) já começou a
 * baixar o chunk nesse meio-tempo, então ele chega antes do texto.
 *
 * Os testes renderizam `SafeMarkdown`/`AiMarkdown` direto (SSR), por isso eles
 * continuam exportados com import estático nos módulos originais.
 */
export const SafeMarkdownLazy = dynamic(() => import('./SafeMarkdown').then((m) => m.SafeMarkdown), {
  ssr: false,
});

export const AiMarkdownLazy = dynamic(() => import('./dashboard/AiMarkdown').then((m) => m.AiMarkdown), {
  ssr: false,
});

/** Começa a baixar o chunk do Markdown antes do texto chegar (uso: `useEffect(preloadMarkdown, [])`). */
export function preloadMarkdown(): void {
  void import('./SafeMarkdown');
  void import('./dashboard/AiMarkdown');
}
