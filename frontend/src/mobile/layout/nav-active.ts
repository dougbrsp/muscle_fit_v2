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

/** Rota atual e a anterior vista pelo shell; `anterior === null` = a entrada atual é a abertura direta. */
export type OrigemRota = { primeira: string | null; atual: string | null; anterior: string | null };

/**
 * Registra a rota (idempotente: a mesma chave repetida, como o efeito em dobro do StrictMode,
 * não muda nada). `primeira` é a entrada inicial do aplicativo na sessão.
 */
export function registrarRota(estado: OrigemRota, chave: string): OrigemRota {
  if (estado.atual === null) return { primeira: chave, atual: chave, anterior: null };
  if (estado.atual === chave) return estado;
  return { primeira: estado.primeira, atual: chave, anterior: estado.atual };
}

/** Voltar à entrada inicial vale como entrada direta: a entrada anterior do histórico pode ser externa. */
export function origemEhInterna(estado: OrigemRota): boolean {
  return estado.anterior !== null && estado.atual !== estado.primeira;
}

let origemRota: OrigemRota = { primeira: null, atual: null, anterior: null };

/** O shell chama a cada troca de rota; separa "veio de outra tela" de "abriu a URL direto". */
export function registrarRotaAtual(chave: string): void {
  origemRota = registrarRota(origemRota, chave);
}

export function temOrigemInterna(): boolean {
  return origemEhInterna(origemRota);
}

/**
 * Editor de refeição dirigido por `/nutricao/?nova=1` (não registra view): o voltar do cabeçalho
 * faz o mesmo que o Cancelar do editor. `null` = não é esse estado.
 */
export function voltarDoEditorNova(
  pathname: string | null | undefined,
  search: string | null | undefined,
  origemInterna: boolean,
): 'back' | 'replace' | null {
  if (normalizePath(pathname) !== '/nutricao') return null;
  if (new URLSearchParams(search ?? '').get('nova') !== '1') return null;
  return origemInterna ? 'back' : 'replace';
}

export type AcaoVoltarCabecalho = 'back' | 'replace-nutricao' | 'abrir-mais';

/** Clique no voltar do cabeçalho (sem href): visão aberta e ?nova=1 voltam; entrada direta vai à rota de origem. */
export function acaoVoltarCabecalho(input: {
  pathname: string | null | undefined;
  search: string | null | undefined;
  origemInterna: boolean;
  temVisaoAberta: boolean;
}): AcaoVoltarCabecalho {
  if (input.temVisaoAberta) return 'back';
  const nova = voltarDoEditorNova(input.pathname, input.search, input.origemInterna);
  if (nova === 'back') return 'back';
  if (nova === 'replace') return 'replace-nutricao';
  return 'abrir-mais';
}
