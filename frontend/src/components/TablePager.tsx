'use client';

import { Group, Pagination } from '@mantine/core';

interface TablePagerProps {
  page: number;
  totalPages: number;
  onChange: (page: number) => void;
  /** Normalmente `showPager` de `usePagination`; sem ele o paginador some. */
  visible?: boolean;
}

/** Paginador padrao das tabelas (mesmo visual de /registro-exercicios). */
export function TablePager({ page, totalPages, onChange, visible = true }: TablePagerProps) {
  if (!visible || totalPages <= 1) return null;
  return (
    <Group justify="center" mt="md">
      <Pagination total={totalPages} value={page} onChange={onChange} color="brand" />
    </Group>
  );
}
