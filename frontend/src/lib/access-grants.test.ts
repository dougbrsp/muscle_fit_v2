import { describe, expect, it } from 'vitest';
import type { AccessCatalog } from './access-admin-types';
import {
  auditActionLabel,
  cellWarning,
  diffGrants,
  groupResourcesByDomain,
  grantsToMatrix,
  matrixToGrants,
  shouldConfirmDiscard,
  validateGroupKey,
  validateGroupName,
  warningsForSave,
} from './access-grants';

const catalog: AccessCatalog = {
  resources: [
    { key: 'a', label: 'A', domain: 'd1', grantable: true, warning: null },
    { key: 'b', label: 'B', domain: 'd2', grantable: false, warning: null },
    { key: 'c', label: 'C', domain: 'd1', grantable: true, warning: 'Cuidado C' },
  ],
  actions: ['ver', 'editar'],
  scopes: ['proprios', 'alunos', 'todos'],
  scope_warnings: { todos: 'Amplo' },
};

describe('access-grants', () => {
  it('grants <-> matriz preserva o conjunto em ordem do catálogo', () => {
    const grants = [
      { resource: 'c', action: 'ver', scope: 'proprios' },
      { resource: 'a', action: 'editar', scope: 'todos' },
    ];
    const matrix = grantsToMatrix(grants);
    expect(matrix['a|editar']).toBe('todos');
    expect(matrixToGrants(matrix, catalog)).toEqual([
      { resource: 'a', action: 'editar', scope: 'todos' },
      { resource: 'c', action: 'ver', scope: 'proprios' },
    ]);
    expect(matrixToGrants({ 'a|ver': '' }, catalog)).toEqual([]);
  });

  it('diff separa novas, alteradas e removidas', () => {
    const before = [
      { resource: 'a', action: 'ver', scope: 'proprios' },
      { resource: 'a', action: 'editar', scope: 'proprios' },
    ];
    const after = [
      { resource: 'a', action: 'ver', scope: 'proprios' },
      { resource: 'a', action: 'editar', scope: 'todos' },
      { resource: 'c', action: 'ver', scope: 'alunos' },
    ];
    const d = diffGrants(before, after);
    expect(d.added).toEqual([after[2]]);
    expect(d.changed).toEqual([after[1]]);
    expect(d.removed).toEqual([]);
    expect(diffGrants(after, []).removed).toHaveLength(3);
  });

  it('avisos só para concessão nova/alterada com texto do catálogo', () => {
    const before = [{ resource: 'a', action: 'ver', scope: 'todos' }];
    const after = [
      { resource: 'a', action: 'ver', scope: 'todos' }, // inalterada: sem aviso
      { resource: 'a', action: 'editar', scope: 'todos' },
      { resource: 'c', action: 'ver', scope: 'proprios' },
      { resource: 'c', action: 'editar', scope: 'todos' },
    ];
    const w = warningsForSave(before, after, catalog);
    expect(w.map((x) => [x.resource, x.action, x.texts])).toEqual([
      ['a', 'editar', ['Amplo']],
      ['c', 'ver', ['Cuidado C']],
      ['c', 'editar', ['Amplo', 'Cuidado C']],
    ]);
    expect(warningsForSave(after, after, catalog)).toEqual([]);
  });

  it('aviso de célula combina escopo e recurso', () => {
    expect(cellWarning(catalog.resources[0], 'proprios', catalog)).toBeNull();
    expect(cellWarning(catalog.resources[2], 'todos', catalog)).toBe('Amplo Cuidado C');
  });

  it('agrupa por domínio e oculta não concedíveis', () => {
    expect(groupResourcesByDomain(catalog.resources, { onlyGrantable: true })).toEqual([
      { domain: 'd1', resources: [catalog.resources[0], catalog.resources[2]] },
    ]);
    expect(groupResourcesByDomain(catalog.resources, { onlyGrantable: false }).map((s) => s.domain)).toEqual([
      'd1',
      'd2',
    ]);
  });

  it('valida chave e nome', () => {
    expect(validateGroupKey('grupo_1')).toBeNull();
    expect(validateGroupKey('Ab')).not.toBeNull();
    expect(validateGroupKey('1abc')).not.toBeNull();
    expect(validateGroupKey('a'.repeat(65))).not.toBeNull();
    expect(validateGroupName('  ')).not.toBeNull();
    expect(validateGroupName('x'.repeat(121))).not.toBeNull();
    expect(validateGroupName('ok')).toBeNull();
  });

  it('rotula ações de auditoria e mantém desconhecidas', () => {
    expect(auditActionLabel('member.add')).toBe('Membro adicionado');
    expect(auditActionLabel('x.y')).toBe('x.y');
  });
});

describe('shouldConfirmDiscard', () => {
  it('só pede confirmação com rascunho sujo e seleção diferente', () => {
    expect(shouldConfirmDiscard(true, 1, 2)).toBe(true);
    expect(shouldConfirmDiscard(true, 1, null)).toBe(true);
    expect(shouldConfirmDiscard(true, 1, 1)).toBe(false);
    expect(shouldConfirmDiscard(false, 1, 2)).toBe(false);
  });
});
