/**
 * Casamento rota -> chave do registry (puro, sem React). Chaves podem ter
 * segmentos dinâmicos `[nome]` (ex.: `/registro-exercicios/[activityId]`).
 * Exata vence dinâmica; entre dinâmicas vence a de mais segmentos literais.
 */

export function normalizePathname(pathname: string | null | undefined): string {
  if (!pathname) return '';
  return pathname.length > 1 && pathname.endsWith('/') ? pathname.slice(0, -1) : pathname;
}

const DYNAMIC_SEGMENT = /^\[[^\]/]+\]$/;

function segmentsOf(path: string): string[] {
  return path.split('/').filter(Boolean);
}

export function matchScreenKey(
  keys: readonly string[],
  pathname: string | null | undefined,
): string | null {
  const path = normalizePathname(pathname);
  if (!path) return null;
  if (keys.includes(path)) return path;
  const parts = segmentsOf(path);
  let best: { key: string; literals: number } | null = null;
  for (const key of keys) {
    const pattern = segmentsOf(key);
    if (pattern.length !== parts.length || !pattern.some((seg) => DYNAMIC_SEGMENT.test(seg))) continue;
    const ok = pattern.every((seg, i) => DYNAMIC_SEGMENT.test(seg) || seg === parts[i]);
    if (!ok) continue;
    const literals = pattern.filter((seg) => !DYNAMIC_SEGMENT.test(seg)).length;
    if (!best || literals > best.literals) best = { key, literals };
  }
  return best?.key ?? null;
}
