import { describe, expect, it } from 'vitest';
import { sanitizeNextPath } from '@/app/LoginClient';
import { sanitizeInternalPath } from './safe-path';

/** SEG-18: `?next=` e links internos só aceitam caminho da própria origem. */

const ACEITOS = [
  '/dashboard',
  '/dashboard/',
  '/modelos-treino/1?x=1',
  '/modelos-treino/1?x=1#secao',
  '/exercicios?q=supino%20reto',
  '/perfil?troque-senha=1',
  '/a%2Fb', // barra codificada no meio do caminho: continua interno
  '/',
];

const RECUSADOS: Array<[string, string]> = [
  ['//evil.com', 'protocol-relative'],
  ['///evil.com', 'tripla barra'],
  ['/\\evil.com', 'barra invertida logo após a barra'],
  ['\\evil.com', 'barra invertida inicial'],
  ['\\\\evil.com', 'UNC'],
  ['/dash\\board', 'barra invertida no meio'],
  ['https://evil.com', 'URL absoluta'],
  ['http://evil.com', 'http'],
  ['javascript:alert(1)', 'esquema javascript'],
  ['JaVaScRiPt:alert(1)', 'esquema com caixa mista'],
  ['data:text/html,x', 'esquema data'],
  ['dashboard', 'relativo sem barra'],
  ['/%2f%2fevil.com', '%2f%2f'],
  ['/%2F%2Fevil.com', '%2F%2F'],
  ['/%5cevil.com', '%5c'],
  ['/%5Cevil.com', '%5C'],
  ['%2f%2fevil.com', 'começa codificado'],
  ['/%252f%252fevil.com', 'duplamente codificado'],
  ['/%255cevil.com', '%5c duplamente codificado'],
  ['/%09/evil.com', 'TAB codificado'],
  ['/%0d%0aSet-Cookie:x', 'CRLF codificado'],
  ['/%E0%A4%A', '% malformado'],
  ['/\t/evil.com', 'TAB literal'],
  ['/\n/evil.com', 'LF literal'],
  ['/\r/evil.com', 'CR literal'],
  ['/\u0000x', 'NUL'],
  ['/\u007fx', 'DEL'],
  ['/ /evil.com', 'espaço literal'],
  [`/${String.fromCharCode(0x200b)}/evil.com`, 'zero-width space'],
  [`/${String.fromCharCode(0x2028)}x`, 'separador de linha'],
  ['', 'vazio'],
];

describe('sanitizeInternalPath (SEG-18)', () => {
  it.each(ACEITOS)('aceita %s', (path) => {
    expect(sanitizeInternalPath(path)).toBe(path);
  });

  it.each(RECUSADOS)('recusa %j (%s)', (path) => {
    expect(sanitizeInternalPath(path)).toBeNull();
  });

  it('recusa null/undefined', () => {
    expect(sanitizeInternalPath(null)).toBeNull();
    expect(sanitizeInternalPath(undefined)).toBeNull();
  });
});

describe('sanitizeNextPath da tela de login', () => {
  it('mantém o caminho interno', () => {
    expect(sanitizeNextPath('/modelos-treino/1?x=1')).toBe('/modelos-treino/1?x=1');
  });

  it.each(['//x', '/\\x', 'https://x', '/%5Cx', '/%2f%2fx', null])('%j cai em /dashboard', (raw) => {
    expect(sanitizeNextPath(raw)).toBe('/dashboard');
  });
});
