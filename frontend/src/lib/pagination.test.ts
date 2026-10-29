import { describe, expect, it } from 'vitest';
import { PAGE_SIZE, paginate } from './pagination';

const range = (n: number) => Array.from({ length: n }, (_, i) => i + 1);

describe('paginate', () => {
  it('usa PAGE_SIZE=20 por padrao', () => {
    expect(PAGE_SIZE).toBe(20);
    const r = paginate(range(45), 1);
    expect(r.pageItems).toEqual(range(20));
    expect(r.totalPages).toBe(3);
    expect(r.showPager).toBe(true);
  });

  it('fatia a ultima pagina parcial', () => {
    const r = paginate(range(45), 3);
    expect(r.pageItems).toEqual([41, 42, 43, 44, 45]);
    expect(r.page).toBe(3);
  });

  it('nao mostra paginador com ate uma pagina', () => {
    expect(paginate(range(20), 1).showPager).toBe(false);
    expect(paginate(range(21), 1).showPager).toBe(true);
  });

  it('lista vazia: 1 pagina, sem itens, sem paginador', () => {
    const r = paginate([], 5);
    expect(r).toEqual({ pageItems: [], page: 1, totalPages: 1, showPager: false });
  });

  it('limita pagina fora do intervalo', () => {
    expect(paginate(range(25), 9).page).toBe(2);
    expect(paginate(range(25), 9).pageItems).toEqual([21, 22, 23, 24, 25]);
    expect(paginate(range(25), 0).page).toBe(1);
    expect(paginate(range(25), -3).page).toBe(1);
    expect(paginate(range(25), Number.NaN).page).toBe(1);
  });

  it('aceita pageSize customizado e ignora valor invalido', () => {
    expect(paginate(range(10), 2, 3).pageItems).toEqual([4, 5, 6]);
    expect(paginate(range(10), 1, 0).pageItems).toEqual(range(10));
  });

  it('nao muta a lista de entrada', () => {
    const items = range(30);
    paginate(items, 2);
    expect(items).toEqual(range(30));
  });
});
