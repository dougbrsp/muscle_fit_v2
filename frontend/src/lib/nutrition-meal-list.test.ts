import { describe, expect, it } from 'vitest';
import { mealHeadline, mealMacrosLine, natureTone } from './nutrition-meal-list';
import type { NutritionTotals } from './nutrition-types';

const item = (label: string) => ({ label });
const totais = (nutrients: Record<string, number | null>, incompletos: string[] = []): NutritionTotals => ({
  nutrients,
  incomplete_fields: incompletos,
  complete: incompletos.length === 0,
  nature: 'DERIVED',
  item_count: 1,
  limitations: [],
});

describe('mealHeadline: Hora - Nome', () => {
  it('um item usa o nome do item', () => {
    expect(mealHeadline({ meal_slot: 'lunch', eaten_at: '12:30', items: [item('Barra de proteína')] as never })).toBe('12:30 - Barra de proteína');
  });
  it('vários itens usam o rótulo da refeição e a quantidade', () => {
    expect(mealHeadline({ meal_slot: 'lunch', eaten_at: '12:30', items: [item('a'), item('b'), item('c')] as never })).toBe('12:30 - Almoço (3 itens)');
  });
  it('sem hora vem só o nome, sem separador', () => {
    expect(mealHeadline({ meal_slot: 'lunch', eaten_at: null, items: [item('a'), item('b')] as never })).toBe('Almoço (2 itens)');
    expect(mealHeadline({ meal_slot: 'lunch', eaten_at: null, items: [item('Banana')] as never })).toBe('Banana');
  });
});

describe('mealMacrosLine: valores do motor, sem recalcular', () => {
  it('formato exato da linha 2', () => {
    const linha = mealMacrosLine(totais({ energy_kcal: 520, protein_g: 32, carbohydrates_g: 60.5, total_fat_g: 14 }));
    expect(linha).toBe('Macros: 520kcal Proteína: 32g Carbo: 60,5g Gordura: 14g');
  });
  it('campo nulo vira traço e nunca 0 falso', () => {
    const linha = mealMacrosLine(totais({ energy_kcal: 520, protein_g: null, carbohydrates_g: 10, total_fat_g: null }));
    expect(linha).toBe('Macros: 520kcal Proteína: - Carbo: 10g Gordura: -');
    expect(linha).not.toMatch(/Proteína: 0|Gordura: 0/);
  });
  it('campo incompleto é mínimo (≥), e zero verdadeiro continua 0', () => {
    const linha = mealMacrosLine(totais({ energy_kcal: 0, protein_g: 12, carbohydrates_g: 1, total_fat_g: 0 }, ['protein_g']));
    expect(linha).toBe('Macros: 0kcal Proteína: ≥12g Carbo: 1g Gordura: 0g');
  });
});

describe('natureTone: amarelo só com estimativa', () => {
  it('catálogo é marca; informado pela pessoa é neutro; estimado é amarelo', () => {
    expect(natureTone('DERIVED', [])).toBe('brand');
    expect(natureTone('USER_ENTERED', [{ nature: 'USER_ENTERED' }])).toBe('gray');
    expect(natureTone('ESTIMATED', [{ nature: 'ESTIMATED' }])).toBe('yellow');
  });
  it('misto: amarelo só se algum item for estimado', () => {
    expect(natureTone('MIXED', [{ nature: 'DERIVED' }, { nature: 'USER_ENTERED' }])).toBe('gray');
    expect(natureTone('MIXED', [{ nature: 'USER_ENTERED' }, { nature: 'ESTIMATED' }])).toBe('yellow');
  });
});
