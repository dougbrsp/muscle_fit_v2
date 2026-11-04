import { MantineProvider } from '@mantine/core';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import type { Meal, MealItem } from '@/lib/nutrition-types';
import { theme } from '@/theme';
import { MealCard, TotalsBadges } from './MealCard';

function html(el: ReturnType<typeof createElement>): string {
  return renderToStaticMarkup(createElement(MantineProvider, { theme }, el));
}

function item(extra: Partial<MealItem> = {}): MealItem {
  return {
    id: 1,
    position: 0,
    food_id: 3,
    portion_id: null,
    portion_label: null,
    label: 'Arroz',
    quantity: 100,
    unit: 'g',
    amount: 100,
    basis: 'g',
    nutrients: { energy_kcal: 130, protein_g: 2.5, carbohydrates_g: 28, total_fat_g: 0.3 },
    nature: 'DERIVED',
    limitations: [],
    food_source: null,
    citation: null,
    field_sources: [],
    ...extra,
  };
}

function refeicao(items: MealItem[], extra: Partial<Meal> = {}): Meal {
  return {
    id: 9,
    user_id: 1,
    local_date: '2026-11-04',
    meal_slot: 'lunch',
    eaten_at: '12:30',
    source: 'manual',
    notes: 'sem sal',
    totals: {
      nutrients: { energy_kcal: 520, protein_g: 32, carbohydrates_g: 60, total_fat_g: 14 },
      incomplete_fields: [],
      complete: true,
      nature: 'DERIVED',
      item_count: items.length,
      limitations: [],
    },
    items,
    engine_version: null,
    created_at: null,
    updated_at: null,
    ...extra,
  };
}

const props = { onEdit: vi.fn(), onDelete: vi.fn(), deleting: false };

describe('MealCard: duas linhas por refeição', () => {
  it('vários itens: rótulo da refeição com a quantidade e a linha de macros do motor', () => {
    const markup = html(createElement(MealCard, { meal: refeicao([item(), item({ id: 2, position: 1 }), item({ id: 3, position: 2 })]), ...props }));
    expect(markup).toContain('12:30 - Almoço (3 itens)');
    expect(markup).toContain('Macros: 520kcal Proteína: 32g Carbo: 60g Gordura: 14g');
  });

  it('um item: o nome do item; sem hora: sem separador', () => {
    const markup = html(createElement(MealCard, { meal: refeicao([item({ label: 'Banana' })], { eaten_at: null }), ...props }));
    expect(markup).toContain('>Banana<');
    expect(markup).not.toContain(' - Banana');
  });

  it('o detalhe (itens, anotação) continua na tela e a lista tem editar e excluir', () => {
    const markup = html(createElement(MealCard, { meal: refeicao([item()]), ...props }));
    expect(markup).toContain('sem sal');
    expect(markup).toContain('Editar refeição');
    expect(markup).toContain('Excluir refeição');
    expect(markup).toContain('aria-expanded="false"');
  });
});

describe('TotalsBadges: selo de natureza', () => {
  const totais = (nature: string) => refeicao([], { totals: { ...refeicao([]).totals, nature } }).totals;

  it('informado pela pessoa usa o rótulo do dono e cor neutra (nunca amarelo)', () => {
    const markup = html(createElement(TotalsBadges, { totals: totais('USER_ENTERED'), items: [item({ nature: 'USER_ENTERED' })] }));
    expect(markup).toContain('Informado por você');
    expect(markup).toContain('gray');
    expect(markup).not.toContain('yellow');
  });

  it('misto sem item estimado é neutro; com item estimado é amarelo', () => {
    const neutro = html(createElement(TotalsBadges, { totals: totais('MIXED'), items: [item(), item({ nature: 'USER_ENTERED' })] }));
    expect(neutro).toContain('Misto');
    expect(neutro).not.toContain('yellow');
    const amarelo = html(createElement(TotalsBadges, { totals: totais('MIXED'), items: [item(), item({ nature: 'ESTIMATED' })] }));
    expect(amarelo).toContain('yellow');
  });
});
