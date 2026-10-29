import { describe, expect, it } from 'vitest';

import { collapsedSlotTitle, weekdayShortLabel } from './weekday-label';

describe('weekdayShortLabel', () => {
  it('abrevia 1=segunda..7=domingo em maiúsculas pt-BR', () => {
    expect([1, 2, 3, 4, 5, 6, 7].map(weekdayShortLabel)).toEqual([
      'SEG',
      'TER',
      'QUA',
      'QUI',
      'SEX',
      'SÁB',
      'DOM',
    ]);
  });

  it('retorna null sem dia ou com valor fora da convenção', () => {
    expect(weekdayShortLabel(null)).toBeNull();
    expect(weekdayShortLabel(undefined)).toBeNull();
    expect(weekdayShortLabel(0)).toBeNull();
    expect(weekdayShortLabel(8)).toBeNull();
  });
});

describe('collapsedSlotTitle', () => {
  it('inclui o dia abreviado entre o número e o nome', () => {
    expect(collapsedSlotTitle(0, 'Peito', 1)).toBe('Dia 1 · SEG · Peito');
    expect(collapsedSlotTitle(5, 'Descanso', 6)).toBe('Dia 6 · SÁB · Descanso');
  });

  it('sem dia, mantém o título atual', () => {
    expect(collapsedSlotTitle(2, 'Costas', null)).toBe('Dia 3 · Costas');
    expect(collapsedSlotTitle(0, '', null)).toBe('Dia 1 · Sem nome');
    expect(collapsedSlotTitle(0, '', 7)).toBe('Dia 1 · DOM · Sem nome');
  });
});
