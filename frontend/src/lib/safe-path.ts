/**
 * SEG-18: validação única de "caminho interno do app" (sem open redirect).
 *
 * Usada pelo `?next=` da tela de login (`src/app/LoginClient.tsx`), pelo
 * interceptor de 401 (`src/lib/api.ts`) e pelos links do Markdown gerado por
 * IA (`src/components/dashboard/AiMarkdown.tsx`).
 *
 * Aceita só caminho relativo à própria origem, começando com UMA "/".
 * Recusa:
 * - `//host` e `/\host` (o navegador trata `\` como `/` e vira URL de outro host);
 * - qualquer `\`, esquema (`javascript:`, `http:`), URL absoluta;
 * - caracteres de controle e espaços "invisíveis" (o navegador remove
 *   TAB/CR/LF da URL, então `/\t/evil` viraria `//evil`);
 * - variantes codificadas (`/%2f%2fevil`, `/%5cevil`, `%09`...), checadas
 *   depois de decodificar (até 3 camadas), e `%` malformado.
 */

// Base fictícia só para resolver o caminho e comparar a origem.
const PROBE_ORIGIN = 'https://internal.invalid';
const MAX_DECODE_ROUNDS = 3;

// Na forma crua: C0 (0x00-0x1F), DEL, C1 (0x80-0x9F) e qualquer espaço ou
// separador Unicode (o navegador sempre entrega o caminho já codificado).
// eslint-disable-next-line no-control-regex
const FORBIDDEN_RAW_CHARS = /[\u0000-\u001F\u007F-\u009F\s\u200B-\u200F\u2028\u2029\uFEFF]/;

// Depois de decodificar: só controle. Espaço literal vindo de `%20` numa busca
// é legítimo (ex.: `/exercicios?q=supino%20reto`).
// eslint-disable-next-line no-control-regex
const FORBIDDEN_DECODED_CHARS = /[\u0000-\u001F\u007F-\u009F]/;

function isUnsafeForm(value: string, forbidden: RegExp): boolean {
  return (
    !value.startsWith('/') ||
    value.startsWith('//') ||
    value.includes('\\') ||
    forbidden.test(value)
  );
}

export function sanitizeInternalPath(raw: string | null | undefined): string | null {
  if (typeof raw !== 'string' || raw.length === 0) return null;
  if (isUnsafeForm(raw, FORBIDDEN_RAW_CHARS)) return null;

  // Variantes codificadas: decodifica camada a camada e reaplica a regra.
  let current = raw;
  for (let round = 0; round < MAX_DECODE_ROUNDS; round += 1) {
    let decoded: string;
    try {
      decoded = decodeURIComponent(current);
    } catch {
      return null; // `%` malformado
    }
    if (decoded === current) break;
    if (isUnsafeForm(decoded, FORBIDDEN_DECODED_CHARS)) return null;
    current = decoded;
  }

  // Última barreira: o próprio parser de URL tem que manter a mesma origem.
  try {
    if (new URL(raw, PROBE_ORIGIN).origin !== PROBE_ORIGIN) return null;
  } catch {
    return null;
  }
  return raw;
}
