import { describe, expect, it } from 'vitest';
import { matchScreenKey, normalizePathname } from './match';

const KEYS = [
  '/dashboard',
  '/dashboard/exercicios/[exerciseId]',
  '/registro-exercicios',
  '/registro-exercicios/[activityId]',
  '/nutricao/plano',
  '/x/[a]/novo',
  '/x/[a]/[b]',
];

describe('matchScreenKey', () => {
  it('rota exata, com ou sem barra final', () => {
    expect(matchScreenKey(KEYS, '/dashboard')).toBe('/dashboard');
    expect(matchScreenKey(KEYS, '/registro-exercicios/')).toBe('/registro-exercicios');
  });
  it('rota dinâmica', () => {
    expect(matchScreenKey(KEYS, '/registro-exercicios/123/')).toBe('/registro-exercicios/[activityId]');
    expect(matchScreenKey(KEYS, '/dashboard/exercicios/9')).toBe('/dashboard/exercicios/[exerciseId]');
  });
  it('mais segmentos literais vencem', () => {
    expect(matchScreenKey(KEYS, '/x/1/novo')).toBe('/x/[a]/novo');
    expect(matchScreenKey(KEYS, '/x/1/2')).toBe('/x/[a]/[b]');
  });
  it('sem registro devolve null (a página de sempre)', () => {
    expect(matchScreenKey(KEYS, '/perfil')).toBeNull();
    expect(matchScreenKey(KEYS, '/dashboard/treinos')).toBeNull();
    expect(matchScreenKey(KEYS, '/registro-exercicios/1/2')).toBeNull();
    expect(matchScreenKey(KEYS, null)).toBeNull();
    expect(matchScreenKey([], '/dashboard')).toBeNull();
  });
  it('normalizePathname', () => {
    expect(normalizePathname('/a/')).toBe('/a');
    expect(normalizePathname('/')).toBe('/');
    expect(normalizePathname(undefined)).toBe('');
  });
});
