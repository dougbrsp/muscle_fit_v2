import { normalizePath } from './primary-nav';

/** O item (por `href`) é a rota atual ou uma subrota dela? Casa por segmento. */
export function isNavGroupItemActive(href: string, pathname: string | null | undefined): boolean {
  const path = normalizePath(pathname);
  const target = normalizePath(href.split('?')[0]);
  return path === target || path.startsWith(`${target}/`);
}

/**
 * Chave de memória de rolagem das raízes das abas (Início, Histórico, hub
 * Evolução); `null` nas demais telas. Voltar a uma aba reabre onde estava.
 */
export function scrollMemoryKey(
  pathname: string | null | undefined,
  search: string | null | undefined,
): string | null {
  const path = normalizePath(pathname);
  if (path === '/dashboard') {
    return new URLSearchParams(search ?? '').get('hub') === 'evolucao' ? 'evolucao' : 'inicio';
  }
  if (path === '/registro-exercicios') return 'historico';
  return null;
}
