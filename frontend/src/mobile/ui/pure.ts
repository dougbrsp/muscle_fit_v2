/**
 * Funções puras dos primitivos (mobile v2). Só apresentação: limites de
 * densidade da linguagem de interface (docs/mobile/DESIGN_IPHONE.md), nenhuma
 * regra de domínio.
 */

/** Dados secundários de um DataCard: no máximo 2 (1 principal + até 2). */
export const MAX_SECONDARY = 2;

/** Colunas de uma comparação tabular: no máximo 3 na largura. */
export const MAX_COLUMNS = 3;

export function pickSecondary<T>(items: readonly T[] | undefined): T[] {
  return (items ?? []).slice(0, MAX_SECONDARY);
}

export function limitColumns<T>(columns: readonly T[]): T[] {
  return columns.slice(0, MAX_COLUMNS);
}

export type RowKind = 'link' | 'button' | 'static';

/** `href` vence `onClick`; sem nenhum dos dois a linha é informativa. */
export function rowKind(props: { href?: string; onClick?: () => void }): RowKind {
  if (props.href) return 'link';
  if (props.onClick) return 'button';
  return 'static';
}

/** Rótulo do botão de filtros: "Filtros" ou "Filtros (n)" (n > 0). */
export function filterButtonLabel(activeCount: number): string {
  return activeCount > 0 ? `Filtros (${activeCount})` : 'Filtros';
}

/** Contador exibido no sheet, nunca negativo nem fracionário. */
export function normalizeCount(n: number): number {
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : 0;
}

/** Estado visível de uma lista de dados: um e só um. */
export type ListState = 'loading' | 'offline' | 'error' | 'empty' | 'ready';

/**
 * - `items === undefined` (sem dado) NUNCA é vazio: é carregando, offline ou erro;
 * - dado existente tem prioridade sobre erro de refetch ou offline (mostra o dado);
 * - sem dado: offline > carregando > erro; sem sinal nenhum, carregando.
 * `count` é o tamanho de `items` (0 se indefinido).
 */
export function listState(input: {
  hasData: boolean;
  count: number;
  isLoading?: boolean;
  isError?: boolean;
  isOffline?: boolean;
}): ListState {
  if (input.hasData) return input.count === 0 ? 'empty' : 'ready';
  if (input.isOffline) return 'offline';
  if (input.isLoading) return 'loading';
  if (input.isError) return 'error';
  return 'loading';
}

/** Valida que o valor escolhido existe nas opções (senão cai na primeira). */
export function resolveSegment<V extends string>(
  options: readonly { value: V }[],
  value: V,
): V | undefined {
  return options.some((o) => o.value === value) ? value : options[0]?.value;
}
