import { Skeleton } from '@mantine/core';
import type { ReactNode } from 'react';
import { limitColumns, listState } from './pure';
import { EmptyState, ErrorState } from './StateViews';
import styles from './ui.module.css';

export type DataColumn<T> = {
  id: string;
  header: string;
  render: (item: T) => ReactNode;
  /** Alinha à direita (números). */
  end?: boolean;
};

type Common<T> = {
  items: readonly T[] | undefined;
  getKey: (item: T) => string | number;
  isLoading?: boolean;
  isError?: boolean;
  onRetry?: () => void;
  emptyTitle?: string;
  emptyDescription?: ReactNode;
  errorTitle?: string;
  /** `rows` (padrão): ListRow numa superfície só. `cards`: DataCard separados. */
  variant?: 'rows' | 'cards';
  'aria-label': string;
};

/**
 * Lista de dados do celular: NUNCA `<table>`. Dois modos, exclusivos:
 * - `renderItem`: cada item vira um ListRow/DataCard (padrão, 1 dado principal);
 * - `columns`: comparação realmente tabular, no máximo 3 colunas, em grade
 *   (`role="table"`), sem rolagem horizontal.
 * Resolve sozinha carregando (skeleton), erro (com tentar de novo), vazio e
 * pronto, para a tela só informar o estado da query.
 */
export function ResponsiveDataList<T>(
  props: Common<T> &
    (
      | { renderItem: (item: T) => ReactNode; columns?: undefined }
      | { columns: readonly DataColumn<T>[]; renderItem?: undefined }
    ),
) {
  const { items, getKey, isLoading, isError, onRetry } = props;
  const list = items ?? [];
  const state = listState({ isLoading, isError, count: list.length });

  if (state === 'loading') {
    return (
      <div aria-busy="true" aria-label="Carregando a lista" role="status">
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} height={56} radius="md" mb={8} />
        ))}
      </div>
    );
  }
  if (state === 'error') {
    return <ErrorState title={props.errorTitle ?? 'Não foi possível carregar'} onRetry={onRetry} />;
  }
  if (state === 'empty') {
    return <EmptyState title={props.emptyTitle ?? 'Nada por aqui ainda'} description={props.emptyDescription} />;
  }

  if (props.columns) {
    const columns = limitColumns(props.columns);
    const template = { gridTemplateColumns: `repeat(${columns.length}, minmax(0, 1fr))` };
    return (
      <div className={styles.groupBody} role="table" aria-label={props['aria-label']}>
        <div role="row" className={`${styles.cols} ${styles.colsHead}`} style={template}>
          {columns.map((c) => (
            <div key={c.id} role="columnheader" className={`${styles.cell} ${c.end ? styles.cellEnd : ''}`}>
              {c.header}
            </div>
          ))}
        </div>
        {list.map((item) => (
          <div key={getKey(item)} role="row" className={styles.cols} style={template}>
            {columns.map((c) => (
              <div key={c.id} role="cell" className={`${styles.cell} ${c.end ? styles.cellEnd : ''}`}>
                {c.render(item)}
              </div>
            ))}
          </div>
        ))}
      </div>
    );
  }

  return (
    <div
      className={props.variant === 'cards' ? styles.cardList : styles.groupBody}
      role="list"
      aria-label={props['aria-label']}
    >
      {list.map((item) => (
        <div key={getKey(item)} role="listitem">
          {props.renderItem(item)}
        </div>
      ))}
    </div>
  );
}
