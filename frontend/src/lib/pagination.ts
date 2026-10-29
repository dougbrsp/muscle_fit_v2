import { useEffect, useState } from 'react';

/** Tamanho padrao de pagina das tabelas paginadas no cliente. */
export const PAGE_SIZE = 20;

export interface PageSlice<T> {
  /** Itens da pagina atual. */
  pageItems: T[];
  /** Pagina efetiva (1-based), ja ajustada ao intervalo valido. */
  page: number;
  /** Total de paginas (minimo 1). */
  totalPages: number;
  /** Mostrar o paginador so quando ha mais itens que uma pagina. */
  showPager: boolean;
}

/**
 * Fatia `items` na pagina pedida (1-based). Funcao pura: a pagina e
 * limitada a [1, totalPages] - util quando a lista encolhe (ex.: exclusao
 * do ultimo item da ultima pagina).
 */
export function paginate<T>(items: readonly T[], page: number, pageSize: number = PAGE_SIZE): PageSlice<T> {
  const size = Number.isFinite(pageSize) && pageSize > 0 ? Math.floor(pageSize) : PAGE_SIZE;
  const totalPages = Math.max(1, Math.ceil(items.length / size));
  const requested = Number.isFinite(page) ? Math.floor(page) : 1;
  const current = Math.min(Math.max(1, requested), totalPages);
  const start = (current - 1) * size;
  return {
    pageItems: items.slice(start, start + size),
    page: current,
    totalPages,
    showPager: items.length > size,
  };
}

export interface UsePaginationOptions {
  pageSize?: number;
  /** Quando muda (ex.: janela de dias), a paginacao volta para a pagina 1. */
  resetKey?: unknown;
}

export interface UsePaginationResult<T> extends PageSlice<T> {
  setPage: (page: number) => void;
}

/** Paginacao no cliente sobre uma lista ja carregada. */
export function usePagination<T>(items: readonly T[], options: UsePaginationOptions = {}): UsePaginationResult<T> {
  const { pageSize = PAGE_SIZE, resetKey } = options;
  const [page, setPage] = useState(1);

  useEffect(() => {
    setPage(1);
  }, [resetKey]);

  return { ...paginate(items, page, pageSize), setPage };
}
