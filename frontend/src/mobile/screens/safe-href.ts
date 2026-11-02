/**
 * `href` vindo da API so vira link se for caminho interno: comeca com uma
 * barra e nao com `//` (protocol-relative) nem `/\` (normalizado por browsers
 * como `//`). Qualquer outra coisa (URL absoluta, `javascript:`, vazio) = null.
 */
export function safeInternalHref(raw: string | null | undefined): string | null {
  if (typeof raw !== 'string' || raw.length === 0) return null;
  if (!raw.startsWith('/')) return null;
  if (raw.startsWith('//') || raw.startsWith('/\\')) return null;
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f]/.test(raw)) return null;
  return raw;
}
