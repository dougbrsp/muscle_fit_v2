// Seleção de grupos do gráfico de tendência por grupo muscular
// (`/dashboard/musculos`, que herdou o gráfico da antiga /grupos-musculares).
//
// A seleção exibida é DERIVADA (seleção do usuário ∩ grupos disponíveis) em
// vez de "podada" por um useEffect com setState. O efeito anterior devolvia
// um array novo a cada execução (`valid.length > 0 ? valid : []`) e dependia
// de `trendGroupOptions`, que era recriado a cada render enquanto a query
// ainda não tinha dados (`data?.trend_rows || []`) — render → efeito →
// setState → render, até o React abortar com o erro #185 ("Maximum update
// depth exceeded").

/** Quantos grupos o gráfico mostra quando o usuário não escolheu nenhum. */
export const DEFAULT_VISIBLE_GROUPS = 6;

/**
 * Mantém só os grupos selecionados que ainda existem nas opções atuais.
 * Devolve a MESMA referência de `selected` quando nada precisa sair, para
 * não invalidar memos nem disparar renders à toa.
 */
export function keepAvailableGroups(selected: string[], available: readonly string[]): string[] {
  if (selected.length === 0) return selected;
  const availableSet = new Set(available);
  const valid = selected.filter((group) => availableSet.has(group));
  return valid.length === selected.length ? selected : valid;
}

/**
 * Grupos efetivamente plotados: a seleção válida do usuário ou, se ela
 * estiver vazia, os `DEFAULT_VISIBLE_GROUPS` primeiros de `options` (que já
 * vêm ordenados por pressão acumulada).
 */
export function resolveVisibleGroups(selectedValid: string[], options: readonly string[]): string[] {
  return selectedValid.length > 0 ? selectedValid : options.slice(0, DEFAULT_VISIBLE_GROUPS);
}
