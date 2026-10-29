import { MantineProvider } from '@mantine/core';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { theme } from '@/theme';
import { REMAINING_COLLAPSED_LIMIT, RemainingExercisesList, type RemainingExerciseRow } from './RemainingExercisesList';

function render(rows: RemainingExerciseRow[]): string {
  return renderToStaticMarkup(createElement(MantineProvider, { theme }, createElement(RemainingExercisesList, { rows })));
}

const row = (ordinal: number, extra: Partial<RemainingExerciseRow> = {}): RemainingExerciseRow => ({
  key: `wpe-${ordinal}`,
  ordinal,
  name: `Exercicio numero ${ordinal}`,
  ...extra,
});

describe('RemainingExercisesList (M64)', () => {
  it('não renderiza nada sem restantes', () => {
    expect(render([])).not.toContain('Exercícios restantes');
  });

  it('mostra ordinal, nome, equipamento e séries na ordem recebida', () => {
    const html = render([
      row(4, { name: 'Remada curvada', equipmentName: 'Barra livre', setsCount: 3 }),
      row(5, { name: 'Rosca direta', setsCount: 1 }),
    ]);
    expect(html).toContain('Depois (2)');
    expect(html.indexOf('Remada curvada')).toBeLessThan(html.indexOf('Rosca direta'));
    expect(html).toContain('>4.<');
    expect(html).toContain('Barra livre · 3 séries');
    expect(html).toContain('1 série');
    expect(html).not.toContain('Ver todos');
  });

  it('colapsa após o limite com "Ver todos"', () => {
    const rows = Array.from({ length: REMAINING_COLLAPSED_LIMIT + 2 }, (_, index) => row(index + 3));
    const html = render(rows);
    expect(html).toContain(`Ver todos (${rows.length})`);
    expect(html).toContain(rows[REMAINING_COLLAPSED_LIMIT - 1].name);
    expect(html).not.toContain(rows[REMAINING_COLLAPSED_LIMIT].name);
  });
});
