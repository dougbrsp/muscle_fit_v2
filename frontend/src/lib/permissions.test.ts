import { describe, expect, it } from 'vitest';
import { SCOPE_ORDER, can } from './permissions';

const perms = {
  dashboards: { ver: 'proprios', editar: 'proprios' },
  'modelos.treino': { ver: 'alunos' },
  'admin.grupos': { ver: 'todos' },
  estranho: { ver: 'galaxia' },
};

describe('SCOPE_ORDER', () => {
  it('é proprios < alunos < todos', () => {
    expect([...SCOPE_ORDER]).toEqual(['proprios', 'alunos', 'todos']);
  });
});

describe('can', () => {
  it('false sem permissões (null/undefined)', () => {
    expect(can(null, 'dashboards')).toBe(false);
    expect(can(undefined, 'dashboards')).toBe(false);
  });
  it('ação padrão é ver', () => {
    expect(can(perms, 'dashboards')).toBe(true);
    expect(can(perms, 'dashboards', 'ver')).toBe(true);
  });
  it('false com recurso ou ação ausente', () => {
    expect(can(perms, 'nutricao.diario')).toBe(false);
    expect(can(perms, 'dashboards', 'excluir')).toBe(false);
    expect(can({}, 'dashboards')).toBe(false);
  });
  it('false com escopo desconhecido, mesmo sem minScope', () => {
    expect(can(perms, 'estranho')).toBe(false);
    expect(can(perms, 'estranho', 'ver', 'proprios')).toBe(false);
  });
  it('respeita o escopo mínimo', () => {
    expect(can(perms, 'dashboards', 'ver', 'proprios')).toBe(true);
    expect(can(perms, 'dashboards', 'ver', 'alunos')).toBe(false);
    expect(can(perms, 'modelos.treino', 'ver', 'alunos')).toBe(true);
    expect(can(perms, 'modelos.treino', 'ver', 'todos')).toBe(false);
    expect(can(perms, 'admin.grupos', 'ver', 'todos')).toBe(true);
    expect(can(perms, 'admin.grupos', 'ver', 'proprios')).toBe(true);
  });
});
