import { describe, expect, it } from 'vitest';

import { DEFAULT_VISIBLE_GROUPS, keepAvailableGroups, resolveVisibleGroups } from './muscle-groups-selection';

describe('keepAvailableGroups', () => {
  it('devolve a mesma referência quando a seleção está vazia (sem setState em loop)', () => {
    const empty: string[] = [];
    expect(keepAvailableGroups(empty, [])).toBe(empty);
    expect(keepAvailableGroups(empty, ['Peito'])).toBe(empty);
  });

  it('devolve a mesma referência quando todos os grupos continuam disponíveis', () => {
    const selected = ['Peito', 'Costas'];
    expect(keepAvailableGroups(selected, ['Costas', 'Peito', 'Ombros'])).toBe(selected);
  });

  it('remove grupos que sumiram das opções, preservando a ordem da seleção', () => {
    expect(keepAvailableGroups(['Ombros', 'Peito', 'Glúteos'], ['Peito', 'Ombros'])).toEqual(['Ombros', 'Peito']);
  });

  it('com opções vazias (query carregando ou com erro) nada fica visível, mas a seleção original não é alterada', () => {
    const selected = ['Peito'];
    expect(keepAvailableGroups(selected, [])).toEqual([]);
    expect(selected).toEqual(['Peito']);
  });
});

describe('resolveVisibleGroups', () => {
  const options = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'];

  it('usa os seis grupos mais pressionados quando não há seleção válida', () => {
    expect(resolveVisibleGroups([], options)).toEqual(options.slice(0, DEFAULT_VISIBLE_GROUPS));
  });

  it('usa a seleção do usuário quando houver', () => {
    const selected = ['H', 'A'];
    expect(resolveVisibleGroups(selected, options)).toBe(selected);
  });

  it('lista vazia de opções resulta em nenhum grupo visível', () => {
    expect(resolveVisibleGroups([], [])).toEqual([]);
  });
});
