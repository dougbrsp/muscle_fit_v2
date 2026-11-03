import { describe, expect, it } from 'vitest';
import {
  MAX_COLUMNS,
  MAX_SECONDARY,
  filterButtonLabel,
  limitColumns,
  listState,
  normalizeCount,
  pickSecondary,
  resolveSegment,
  rowKind,
} from './pure';

describe('limites de densidade', () => {
  it('DataCard: no máximo 2 dados secundários', () => {
    expect(MAX_SECONDARY).toBe(2);
    expect(pickSecondary([1, 2, 3, 4])).toEqual([1, 2]);
    expect(pickSecondary(undefined)).toEqual([]);
  });
  it('comparação tabular: no máximo 3 colunas', () => {
    expect(MAX_COLUMNS).toBe(3);
    expect(limitColumns(['a', 'b', 'c', 'd', 'e'])).toEqual(['a', 'b', 'c']);
    expect(limitColumns(['a'])).toEqual(['a']);
  });
});

describe('ListRow', () => {
  it('href vence onClick; sem nenhum é estática', () => {
    expect(rowKind({ href: '/x', onClick: () => undefined })).toBe('link');
    expect(rowKind({ onClick: () => undefined })).toBe('button');
    expect(rowKind({})).toBe('static');
  });
});

describe('FilterSheet', () => {
  it('rótulo com contador só quando há filtro ativo', () => {
    expect(filterButtonLabel(0)).toBe('Filtros');
    expect(filterButtonLabel(3)).toBe('Filtros (3)');
  });
  it('contador nunca negativo, NaN ou fracionário', () => {
    expect(normalizeCount(-2)).toBe(0);
    expect(normalizeCount(Number.NaN)).toBe(0);
    expect(normalizeCount(2.9)).toBe(2);
  });
});

describe('ResponsiveDataList', () => {
  it('sem dado nunca é vazio: offline > carregando > erro; padrão carregando', () => {
    expect(listState({ hasData: false, count: 0 })).toBe('loading');
    expect(listState({ hasData: false, count: 0, isLoading: true, isError: true })).toBe('loading');
    expect(listState({ hasData: false, count: 0, isError: true })).toBe('error');
    expect(listState({ hasData: false, count: 0, isOffline: true, isLoading: true })).toBe('offline');
  });
  it('dado existente vence erro de refetch e offline', () => {
    expect(listState({ hasData: true, count: 5, isError: true, isOffline: true })).toBe('ready');
    expect(listState({ hasData: true, count: 0, isError: true })).toBe('empty');
    expect(listState({ hasData: true, count: 1 })).toBe('ready');
  });
});

describe('SegmentedFilter', () => {
  const options = [{ value: 'a' }, { value: 'b' }] as const;
  it('valor desconhecido cai na primeira opção', () => {
    expect(resolveSegment(options, 'b')).toBe('b');
    expect(resolveSegment(options, 'z' as 'a')).toBe('a');
    expect(resolveSegment([], 'a')).toBeUndefined();
  });
});
