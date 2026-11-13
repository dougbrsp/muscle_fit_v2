import type { AccessCatalog, AccessGrant, AccessResource } from './access-admin-types';

// Funções puras da tela "Grupos e acessos". Nenhuma regra de permissão aqui:
// recursos, ações, escopos e avisos vêm do catálogo da API; o backend decide.

/** Matriz `recurso|ação -> escopo` (ausente = sem concessão). */
export type GrantMatrix = Record<string, string>;

export const cellKey = (resource: string, action: string): string => `${resource}|${action}`;

export function grantsToMatrix(grants: readonly AccessGrant[]): GrantMatrix {
  const matrix: GrantMatrix = {};
  for (const g of grants) matrix[cellKey(g.resource, g.action)] = g.scope;
  return matrix;
}

/** Matriz -> lista de concessões, em ordem estável do catálogo; ignora células vazias. */
export function matrixToGrants(matrix: GrantMatrix, catalog: AccessCatalog): AccessGrant[] {
  const out: AccessGrant[] = [];
  const known = new Set<string>();
  for (const resource of catalog.resources) {
    for (const action of catalog.actions) {
      const key = cellKey(resource.key, action);
      known.add(key);
      const scope = matrix[key];
      if (scope) out.push({ resource: resource.key, action, scope });
    }
  }
  // Concessões de recurso/ação fora do catálogo atual são preservadas (não some silenciosamente).
  for (const [key, scope] of Object.entries(matrix)) {
    if (known.has(key) || !scope) continue;
    const [resource, action] = key.split('|');
    out.push({ resource, action, scope });
  }
  return out;
}

export type GrantsDiff = { added: AccessGrant[]; changed: AccessGrant[]; removed: AccessGrant[] };

export function diffGrants(before: readonly AccessGrant[], after: readonly AccessGrant[]): GrantsDiff {
  const prev = grantsToMatrix(before);
  const next = grantsToMatrix(after);
  const diff: GrantsDiff = { added: [], changed: [], removed: [] };
  for (const g of after) {
    const old = prev[cellKey(g.resource, g.action)];
    if (old === undefined) diff.added.push(g);
    else if (old !== g.scope) diff.changed.push(g);
  }
  for (const g of before) {
    if (next[cellKey(g.resource, g.action)] === undefined) diff.removed.push(g);
  }
  return diff;
}

export type GrantWarning = { resource: string; label: string; action: string; scope: string; texts: string[] };

/**
 * Avisos a confirmar ao salvar: só concessões novas ou alteradas cujo escopo
 * tem texto em `scope_warnings` ou cujo recurso tem `warning`. Os textos vêm
 * do catálogo, sem duplicar o mesmo texto na mesma concessão.
 */
export function warningsForSave(
  before: readonly AccessGrant[],
  after: readonly AccessGrant[],
  catalog: AccessCatalog,
): GrantWarning[] {
  const { added, changed } = diffGrants(before, after);
  const byKey = new Map(catalog.resources.map((r) => [r.key, r]));
  const result: GrantWarning[] = [];
  for (const g of [...added, ...changed]) {
    const resource = byKey.get(g.resource);
    const texts: string[] = [];
    const scopeText = catalog.scope_warnings[g.scope];
    if (scopeText) texts.push(scopeText);
    if (resource?.warning && !texts.includes(resource.warning)) texts.push(resource.warning);
    if (texts.length) {
      result.push({ resource: g.resource, label: resource?.label ?? g.resource, action: g.action, scope: g.scope, texts });
    }
  }
  return result;
}

/** Aviso visual de uma célula (matriz): texto do escopo escolhido e/ou do recurso. */
export function cellWarning(
  resource: AccessResource | undefined,
  scope: string | undefined,
  catalog: AccessCatalog,
): string | null {
  const parts: string[] = [];
  const scopeText = scope ? catalog.scope_warnings[scope] : null;
  if (scopeText) parts.push(scopeText);
  if (resource?.warning) parts.push(resource.warning);
  return parts.length ? parts.join(' ') : null;
}

export type DomainSection = { domain: string; resources: AccessResource[] };

/** Agrupa recursos por domínio mantendo a ordem do catálogo; esconde não concedíveis se pedido. */
export function groupResourcesByDomain(
  resources: readonly AccessResource[],
  opts: { onlyGrantable: boolean },
): DomainSection[] {
  const sections: DomainSection[] = [];
  for (const resource of resources) {
    if (opts.onlyGrantable && !resource.grantable) continue;
    let section = sections.find((s) => s.domain === resource.domain);
    if (!section) {
      section = { domain: resource.domain, resources: [] };
      sections.push(section);
    }
    section.resources.push(resource);
  }
  return sections;
}

export const GROUP_KEY_PATTERN = /^[a-z][a-z0-9_]{2,63}$/;

/** Validação de formato da chave (contrato da API); devolve a mensagem ou `null`. */
export function validateGroupKey(value: string): string | null {
  if (GROUP_KEY_PATTERN.test(value)) return null;
  return 'Use de 3 a 64 caracteres: letra minúscula no início, depois minúsculas, números ou "_".';
}

export const GROUP_NAME_MAX = 120;
export const GROUP_DESCRIPTION_MAX = 500;

export function validateGroupName(value: string): string | null {
  const name = value.trim();
  if (!name) return 'Informe o nome.';
  if (name.length > GROUP_NAME_MAX) return `O nome aceita até ${GROUP_NAME_MAX} caracteres.`;
  return null;
}

const AUDIT_ACTION_LABELS: Record<string, string> = {
  'group.create': 'Grupo criado',
  'group.update': 'Grupo editado',
  'group.grants.replace': 'Concessões substituídas',
  'group.delete': 'Grupo excluído',
  'member.add': 'Membro adicionado',
  'member.remove': 'Membro removido',
};

export function auditActionLabel(action: string): string {
  return AUDIT_ACTION_LABELS[action] ?? action;
}

/** Rótulos de apresentação de ação/escopo; valores desconhecidos aparecem como vêm da API. */
const PRESENTATION_LABELS: Record<string, string> = {
  ver: 'Ver',
  editar: 'Editar',
  excluir: 'Excluir',
  proprios: 'Próprios',
  alunos: 'Alunos',
  todos: 'Todos',
};

export function presentationLabel(value: string): string {
  return PRESENTATION_LABELS[value] ?? value;
}

/** Trocar a seleção de grupo descarta o rascunho de concessões: só pede confirmação se há rascunho sujo e a seleção muda. */
export function shouldConfirmDiscard(dirty: boolean, currentId: number | null, nextId: number | null): boolean {
  return dirty && currentId !== nextId;
}
