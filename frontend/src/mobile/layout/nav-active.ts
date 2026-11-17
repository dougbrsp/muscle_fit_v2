import { normalizePath } from './primary-nav';

/** O item (por `href`) é a rota atual ou uma subrota dela? Casa por segmento. */
export function isNavGroupItemActive(href: string, pathname: string | null | undefined): boolean {
  const path = normalizePath(pathname);
  const target = normalizePath(href.split('?')[0]);
  return path === target || path.startsWith(`${target}/`);
}

/**
 * Chave de memória de rolagem das raízes das abas (Início, hub
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
  return null;
}

let navegouDentroDoApp = false;

/** O shell chama a cada troca de rota; separa "veio de outra tela" de "abriu a URL direto". */
export function marcarNavegacaoInterna(): void {
  navegouDentroDoApp = true;
}

export function temOrigemInterna(): boolean {
  return navegouDentroDoApp;
}

/**
 * Editor de refeição dirigido por `/nutricao/?nova=1` (não registra view): o voltar do cabeçalho
 * faz o mesmo que o Cancelar do editor. `null` = não é esse estado.
 */
export function voltarDoEditorNova(
  pathname: string | null | undefined,
  search: string | null | undefined,
): 'back' | 'replace' | null {
  if (normalizePath(pathname) !== '/nutricao') return null;
  if (new URLSearchParams(search ?? '').get('nova') !== '1') return null;
  return navegouDentroDoApp ? 'back' : 'replace';
}
