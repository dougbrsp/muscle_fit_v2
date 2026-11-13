// Permissões por grupos (RBAC, ADR 0011), lado do frontend. Só LEITURA do que
// `GET /auth/me` devolve em `permissions` ({recurso: {acao: escopo}}): o backend
// é a barreira real; aqui só se esconde o que o usuário não pode usar. Nenhuma
// regra de acesso nasce neste arquivo.

/**
 * Escopos em ordem de abrangência (proprios < alunos < todos). Espelho de
 * `SCOPES` em `api/app/access/catalog.py`; a paridade é provada por
 * `api/tests/unit/test_access_paridade_escopos_frontend.py`, que lê este array.
 */
export const SCOPE_ORDER = ['proprios', 'alunos', 'todos'] as const;

export type Scope = (typeof SCOPE_ORDER)[number];

export type Permissions = Record<string, Record<string, string>>;

/**
 * O usuário tem `resource`/`action` (padrão `ver`) com escopo ao menos `minScope`?
 * `false` sem permissões (null/undefined: carregando, erro ou backend
 * indisponível), com recurso ou ação ausente, escopo desconhecido ou abaixo do
 * mínimo pedido.
 */
export function can(
  perms: Permissions | null | undefined,
  resource: string,
  action: string = 'ver',
  minScope?: Scope,
): boolean {
  if (!perms) return false;
  const scope = perms[resource]?.[action];
  if (typeof scope !== 'string') return false;
  const rank = (SCOPE_ORDER as readonly string[]).indexOf(scope);
  if (rank < 0) return false;
  if (minScope === undefined) return true;
  return rank >= SCOPE_ORDER.indexOf(minScope);
}
