import { MantineProvider } from '@mantine/core';
import { IconUser } from '@tabler/icons-react';
import { createElement as h, type ReactElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import {
  DataCard,
  ListBlock,
  ListGroup,
  ListRow,
  ResponsiveDataList,
  ScreenHeader,
  SegmentedFilter,
  StatTile,
} from '.';

const html = (node: ReactElement) => renderToStaticMarkup(node);
const themed = (node: ReactElement) => html(h(MantineProvider, null, node));
const noop = () => undefined;

describe('primitivos (marcação)', () => {
  it('ScreenHeader: um h1 com título e subtítulo', () => {
    const out = html(h(ScreenHeader, { title: 'Evolução', subtitle: 'Resumo' }));
    expect(out.match(/<h1/g)).toHaveLength(1);
    expect(out).toContain('Evolução');
    expect(out).toContain('Resumo');
  });

  it('ListRow: link, botão e estática têm o elemento certo', () => {
    expect(html(h(ListRow, { title: 'A', href: '/a' }))).toMatch(/^<a /);
    expect(html(h(ListRow, { title: 'B', onClick: noop }))).toMatch(/^<button type="button"/);
    expect(html(h(ListRow, { title: 'C' }))).toMatch(/^<div /);
    expect(html(h(ListRow, { title: 'D', icon: IconUser, href: '/d', current: true }))).toContain(
      'aria-current="page"',
    );
  });

  it('ListGroup: título como h2 e região nomeada', () => {
    const out = html(h(ListGroup, { title: 'Treino', footer: 'nota', children: h(ListBlock, { children: 'x' }) }));
    expect(out).toContain('<h2');
    expect(out).toContain('aria-label="Treino"');
    expect(out).toContain('nota');
  });

  it('DataCard: mostra no máximo 2 dados secundários', () => {
    const out = html(
      h(DataCard, {
        title: 'Supino',
        value: '82 kg',
        secondary: [
          { label: 'a', value: '1' },
          { label: 'b', value: '2' },
          { label: 'c', value: '3' },
        ],
      }),
    );
    expect(out).toContain('82 kg');
    expect(out).toContain('>a<');
    expect(out).toContain('>b<');
    expect(out).not.toContain('>c<');
  });

  it('StatTile exibe rótulo, valor e dica', () => {
    const out = html(h(StatTile, { label: 'Séries', value: '12', hint: 'na semana' }));
    expect(out).toContain('Séries');
    expect(out).toContain('12');
    expect(out).toContain('na semana');
  });

  it('SegmentedFilter: radiogroup com um item marcado', () => {
    const out = html(
      h(SegmentedFilter<'a' | 'b'>, {
        label: 'Período',
        value: 'b',
        onChange: noop,
        options: [
          { value: 'a', label: 'A' },
          { value: 'b', label: 'B' },
        ],
      }),
    );
    expect(out).toContain('role="radiogroup"');
    expect(out.match(/aria-checked="true"/g)).toHaveLength(1);
  });

  type Item = { id: number; a: string; b: string; c: string; d: string };
  const items: Item[] = [{ id: 1, a: 'x', b: 'y', c: 'z', d: 'w' }];
  const List = ResponsiveDataList<Item>;

  it('ResponsiveDataList: nunca <table>; colunas limitadas a 3', () => {
    const cols = (['a', 'b', 'c', 'd'] as const).map((k) => ({
      id: k,
      header: k.toUpperCase(),
      render: (i: Item) => i[k],
    }));
    const grid = html(h(List, { items, getKey: (i) => i.id, columns: cols, 'aria-label': 't' }));
    expect(grid).not.toContain('<table');
    expect(grid).toContain('role="table"');
    expect(grid.match(/role="columnheader"/g)).toHaveLength(3);
    const rows = html(
      h(List, {
        items,
        getKey: (i) => i.id,
        renderItem: (i) => h(ListRow, { title: i.a }),
        'aria-label': 'l',
      }),
    );
    expect(rows).not.toContain('<table');
    expect(rows).toContain('role="list"');
  });

  it('ResponsiveDataList: estados vazio, erro e carregando', () => {
    const base = { getKey: (i: Item) => i.id, renderItem: () => null, 'aria-label': 'l' };
    expect(themed(h(List, { ...base, items: [], emptyTitle: 'Vazio' }))).toContain('Vazio');
    expect(
      themed(h(List, { ...base, items: undefined, isError: true, errorTitle: 'Falhou' })),
    ).toContain('Falhou');
    expect(themed(h(List, { ...base, items: undefined, isLoading: true }))).toContain(
      'aria-busy="true"',
    );
  });
});
